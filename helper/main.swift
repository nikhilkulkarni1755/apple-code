// JSON-lines bridge to Apple's on-device model. One request per stdin line; events on stdout.
//   {"cmd":"run","instructions":"..","prompt":"..","tools":[{name,description,parameters:[{name,type,description,optional}]}],
//    "max_tool_calls":8,"temperature":0.2}
//     -> {"event":"tool_call","id":1,"name":"..","args":{..}}   (reply on stdin: {"id":1,"result":".."})
//     -> {"event":"done","content":"..","stopped":false} | {"event":"error","kind":"context_overflow|guardrail|other","message":".."}
//   {"cmd":"count","instructions":"..","prompt":"..","tools":[..]} -> {"event":"count","tools":N,"instructions":N,"prompt":N}
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
    var calls = 0
    init(maxCalls: Int) { self.maxCalls = maxCalls }

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
        let args = try JSONSerialization.jsonObject(with: Data(arguments.jsonString.utf8))
        return try bridge.call(name: name, args: args)
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

func handle(_ req: [String: Any]) async {
    let model = SystemLanguageModel.default
    let instructions = req["instructions"] as? String ?? ""
    let prompt = req["prompt"] as? String ?? ""
    let bridge = Bridge(maxCalls: req["max_tool_calls"] as? Int ?? 8)
    do {
        let tools = try makeTools(req["tools"] as? [[String: Any]] ?? [], bridge: bridge)
        if req["cmd"] as? String == "count" {
            emit(["event": "count",
                  "tools": try await model.tokenCount(for: tools as [any Tool]),
                  "instructions": try await model.tokenCount(for: Instructions(instructions)),
                  "prompt": try await model.tokenCount(for: prompt)])
            return
        }
        let session = LanguageModelSession(model: model, tools: tools, instructions: instructions)
        let options = GenerationOptions(temperature: req["temperature"] as? Double)
        let r = try await session.respond(to: prompt, options: options)
        emit(["event": "done", "content": r.content, "stopped": false])
    } catch let e as LanguageModelSession.ToolCallError where e.underlyingError is StopError {
        emit(["event": "done", "content": "", "stopped": true])
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
