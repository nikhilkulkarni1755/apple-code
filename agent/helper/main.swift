// JSON-lines bridge to Apple's on-device model. One request per stdin line; events on stdout.
//   {"cmd":"run","instructions":"..","prompt":"..","tools":[{name,description,parameters:[{name,type,description,optional}]}],
//    "max_tool_calls":8,"temperature":0.2}
//     -> {"event":"tool_call","id":1,"name":"..","args":{..}}   (reply on stdin: {"id":1,"result":".."})
//     -> {"event":"done","content":"..","stopped":false} | {"event":"error","kind":"context_overflow|guardrail|other","message":".."}
//   a run request may carry "budget_tokens": the session is stopped at the next tool call once about that many tokens are used,
//   and the done event then says "reason":"budget" (or "calls" when max_tool_calls ended it); "estimated" is the running count.
//   {"cmd":"status"} -> {"event":"status","availability":"available" or the reason the model cannot be used}
//   {"cmd":"count","instructions":"..","prompt":"..","tools":[..]} -> {"event":"count","tools":N,"instructions":N,"prompt":N,"context":N}
import Foundation
import FoundationModels

struct StopError: Error {}

func emit(_ obj: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: obj)
    print(String(data: data, encoding: .utf8)!)
    fflush(stdout)
}

func parse(_ line: String) -> [String: Any]? {
    try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any]
}

final class Bridge: @unchecked Sendable {
    let lock = NSLock()
    let maxCalls: Int
    let budget: Int          // stop the session once about this many tokens are used (0 = no budget)
    var calls = 0
    var used = 0             // running estimate of tokens in the session: instructions + tools + prompt + every tool call and result
    var reached = false
    init(maxCalls: Int, budget: Int) { self.maxCalls = maxCalls; self.budget = budget }

    // The running count undercounts the framework's own formatting by 5 to 20% (measured), so it is scaled up: better to wind down a
    // little early than a little late.
    static let safety = 1.25
    var corrected: Int { Int(Double(used) * Bridge.safety) }

    func add(_ tokens: Int) {
        lock.lock(); defer { lock.unlock() }
        used += tokens
        if budget > 0 && corrected >= budget { reached = true }
    }

    func call(name: String, args: Any) throws -> String {
        lock.lock(); defer { lock.unlock() }
        calls += 1
        if calls > maxCalls { throw StopError() }
        emit(["event": "tool_call", "id": calls, "name": name, "args": args])
        guard let line = readLine(), let reply = parse(line), reply["id"] as? Int == calls else { throw StopError() }
        return reply["result"] as? String ?? ""
    }
}

struct BridgeTool: Tool {
    let name: String
    let description: String
    let parameters: GenerationSchema
    let bridge: Bridge

    func call(arguments: GeneratedContent) async throws -> String {
        if bridge.reached { throw StopError() }   // the budget was crossed: no more tool calls, the session winds down here
        let args = try JSONSerialization.jsonObject(with: Data(arguments.jsonString.utf8))
        let result = try bridge.call(name: name, args: args)
        if bridge.budget > 0 {
            let model = SystemLanguageModel.default
            bridge.add(((try? await model.tokenCount(for: arguments.jsonString)) ?? 0) + ((try? await model.tokenCount(for: result)) ?? 0))
        }
        return result
    }
}

func makeTools(_ specs: [[String: Any]], bridge: Bridge) throws -> [BridgeTool] {
    try specs.map { t in
        let props = (t["parameters"] as? [[String: Any]] ?? []).map { p -> DynamicGenerationSchema.Property in
            let schema: DynamicGenerationSchema
            switch p["type"] as? String {
            case "integer": schema = .init(type: Int.self)
            case "number": schema = .init(type: Double.self)
            case "boolean": schema = .init(type: Bool.self)
            default: schema = .init(type: String.self)
            }
            return .init(name: p["name"] as! String, description: p["description"] as? String, schema: schema,
                         isOptional: p["optional"] as? Bool ?? false)
        }
        let name = t["name"] as! String
        let root = DynamicGenerationSchema(name: name + "Args", properties: props)
        return BridgeTool(name: name, description: t["description"] as? String ?? "",
                          parameters: try GenerationSchema(root: root, dependencies: []), bridge: bridge)
    }
}

func used(_ session: LanguageModelSession?) async -> Int {
    guard let session else { return 0 }
    return (try? await SystemLanguageModel.default.tokenCount(for: Array(session.transcript))) ?? 0
}

func handle(_ req: [String: Any]) async {
    let model = SystemLanguageModel.default
    let instructions = req["instructions"] as? String ?? ""
    let prompt = req["prompt"] as? String ?? ""
    if req["cmd"] as? String == "status" {
        emit(["event": "status", "availability": "\(model.availability)"])
        return
    }
    let bridge = Bridge(maxCalls: req["max_tool_calls"] as? Int ?? 8, budget: req["budget_tokens"] as? Int ?? 0)
    var session: LanguageModelSession? = nil
    do {
        let tools = try makeTools(req["tools"] as? [[String: Any]] ?? [], bridge: bridge)
        if req["cmd"] as? String == "count" {
            emit(["event": "count",
                  "tools": try await model.tokenCount(for: tools as [any Tool]),
                  "instructions": try await model.tokenCount(for: Instructions(instructions)),
                  "prompt": try await model.tokenCount(for: prompt),
                  "context": model.contextSize])
            return
        }
        if bridge.budget > 0 {
            bridge.add(try await model.tokenCount(for: Instructions(instructions)) + model.tokenCount(for: tools as [any Tool]) + model.tokenCount(for: prompt))
        }
        session = LanguageModelSession(model: model, tools: tools, instructions: instructions)
        let options = GenerationOptions(temperature: req["temperature"] as? Double)
        let r = try await session!.respond(to: prompt, options: options)
        emit(["event": "done", "content": r.content, "stopped": false, "tokens": await used(session), "estimated": bridge.corrected, "context": model.contextSize])
    } catch let e as LanguageModelSession.ToolCallError where e.underlyingError is StopError {
        emit(["event": "done", "content": "", "stopped": true, "reason": bridge.reached ? "budget" : "calls",
              "tokens": await used(session), "estimated": bridge.corrected, "context": model.contextSize])
    } catch let e as LanguageModelSession.GenerationError {
        var kind = "other"
        if case .exceededContextWindowSize = e { kind = "context_overflow" }
        if case .guardrailViolation = e { kind = "guardrail" }
        emit(["event": "error", "kind": kind, "message": e.localizedDescription])
    } catch {
        emit(["event": "error", "kind": "other", "message": "\(error)"])
    }
}

while let line = readLine() {
    guard let req = parse(line) else { emit(["event": "error", "kind": "other", "message": "bad json"]); continue }
    await handle(req)
}
