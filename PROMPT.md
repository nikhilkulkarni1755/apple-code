mkdir dino-game && cd dino-game && mkdir tests

cat > tests/test_score.py <<'EOF'
from score import score


def test_no_progress_scores_zero():
    assert score(0, 0) == 0


def test_each_cactus_jumped_is_five_points():
    assert score(1, 0) == 5
    assert score(3, 0) == 15


def test_each_second_alive_is_half_a_point():
    assert score(0, 2) == 1
    assert score(0, 10) == 5


def test_total_is_floored():
    assert score(0, 1) == 0
    assert score(1, 1) == 5
    assert score(1, 3) == 6
    assert score(0, 1.9) == 0
EOF

cat > tests/test_collide.py <<'EOF'
from collide import collides


def test_boxes_that_overlap_collide():
    assert collides((0, 0, 4, 4), (3, 3, 4, 4)) is True


def test_boxes_apart_do_not_collide():
    assert collides((0, 0, 4, 4), (10, 0, 4, 4)) is False
    assert collides((0, 0, 4, 4), (0, 10, 4, 4)) is False


def test_boxes_that_only_touch_do_not_collide():
    assert collides((0, 0, 4, 4), (4, 0, 4, 4)) is False
EOF

cat > tests/test_dino.py <<'EOF'
from dino import Dino


def test_new_dino_is_on_the_ground():
    d = Dino()
    assert d.y == 0
    assert d.on_ground is True


def test_jump_then_update_rises():
    d = Dino()
    d.jump()
    d.update()
    assert d.y == 4
    assert d.on_ground is False


def test_jump_while_in_the_air_is_ignored():
    d = Dino()
    d.jump()
    d.update()
    d.jump()
    d.update()
    assert d.y == 7


def test_dino_lands_and_stays_at_zero():
    d = Dino()
    d.jump()
    for _ in range(40):
        d.update()
        assert d.y >= 0
    assert d.y == 0
    assert d.on_ground is True


def test_update_on_the_ground_does_nothing():
    d = Dino()
    d.update()
    assert d.y == 0
EOF

cat > tests/test_keys.py <<'EOF'
from keys import handle_key


def test_space_and_w_jump_while_alive():
    assert handle_key(" ", True) == "jump"
    assert handle_key("w", True) == "jump"


def test_jump_keys_do_nothing_when_dead():
    assert handle_key(" ", False) is None


def test_r_restarts_only_when_dead():
    assert handle_key("r", False) == "restart"
    assert handle_key("r", True) is None


def test_e_exits_in_any_state():
    assert handle_key("e", True) == "exit"
    assert handle_key("e", False) == "exit"


def test_other_keys_do_nothing():
    assert handle_key("x", True) is None
    assert handle_key("", False) is None
EOF

cat > tests/test_game.py <<'EOF'
from game import Game


def test_new_game_is_alive_with_zero_score():
    g = Game()
    assert g.alive is True
    assert g.score == 0


def test_ten_steps_are_one_second():
    g = Game()
    for _ in range(10):
        g.step()
    assert g.score == 0
    for _ in range(10):
        g.step()
    assert g.score == 1


def test_running_into_a_cactus_ends_the_game():
    g = Game()
    g.add_cactus(8)
    g.step()
    g.step()
    assert g.alive is False


def test_a_dead_game_does_not_advance():
    g = Game()
    g.add_cactus(5)
    g.step()
    assert g.alive is False
    before = g.score
    for _ in range(30):
        g.step()
    assert g.score == before


def test_jumping_a_cactus_scores_five_once():
    g = Game()
    g.dino.jump()
    g.add_cactus(8)
    for _ in range(5):
        g.step()
    assert g.alive is True
    assert g.score == 5
    for _ in range(15):
        g.step()
    assert g.alive is True
    assert g.score == 6


def test_restart_gives_a_fresh_game():
    g = Game()
    g.add_cactus(5)
    g.step()
    g.restart()
    assert g.alive is True
    assert g.score == 0
EOF

apple-code solve "Make the tests in tests/test_score.py pass by creating score.py with score(cacti_jumped, seconds_alive), which returns the floor of 5 points per cactus jumped plus 0.5 per second alive."
apple-code solve "Make the tests in tests/test_collide.py pass by creating collide.py with collides(a, b) for (x, y, w, h) boxes."
apple-code solve "Make the tests in tests/test_dino.py pass by creating dino.py with a Dino class for the T-Rex jump physics."
apple-code solve "Make the tests in tests/test_keys.py pass by creating keys.py with handle_key(key, alive) for the keyboard controls."
apple-code solve "Make the tests in tests/test_game.py pass by creating game.py with a Game class that uses dino.py, score.py and collide.py. The whole project is the Chrome no-internet dino game in Python in the CLI, using the curses framework for the interactive board: cacti rush towards the T-Rex, the keyboard makes it jump, each cactus jumped is +5 points, each second alive is +0.5, touching a cactus ends the game with the floor of the total as the final score, r restarts and e exits."
apple-code test tests
apple-code solve "Create play.py, a curses program that draws the board with the T-Rex and the cacti rushing towards it, runs Game from game.py, passes keys through handle_key from keys.py, shows the score, and on game over shows the floored score and waits for r to restart or e to exit. The whole project is the Chrome no-internet dino game in Python in the CLI, using the curses framework for the interactive board: cacti rush towards the T-Rex, the keyboard makes it jump, each cactus jumped is +5 points, each second alive is +0.5, touching a cactus ends the game with the floor of the total as the final score, r restarts and e exits."
apple-code recap
