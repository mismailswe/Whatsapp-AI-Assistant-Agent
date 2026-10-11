from types import SimpleNamespace as Info

from app.lang import detect_incoming_language, filter_history_for_match, pick_language, reply_looks_urdu


def test_urdu_and_english_are_kept():
    assert pick_language(Info(language="ur")) == "ur"
    assert pick_language(Info(language="en")) == "en"


def test_other_guesses_fall_back_to_the_likelier_of_urdu_or_english():
    assert pick_language(Info(language="fa", all_language_probs=[("fa", 0.5), ("ur", 0.3), ("en", 0.1)])) == "ur"
    assert pick_language(Info(language="de", all_language_probs=[("de", 0.5), ("en", 0.4), ("ur", 0.01)])) == "en"
    assert pick_language(Info(language="hi")) == "ur"


def test_detect_incoming_language():
    assert detect_incoming_language("How are you today?") == "en"
    assert detect_incoming_language("Where are you") == "en"
    assert detect_incoming_language("Hi") == "en"
    assert detect_incoming_language("theek hai tum kahan ho") == "ur"
    assert detect_incoming_language("main college mein hun") == "ur"
    assert detect_incoming_language("آپ کیسے ہیں") == "ur"
    assert reply_looks_urdu("main theek hun tum kaise ho?")
    hist = [
        {"role": "user", "content": "How are you today?"},
        {"role": "assistant", "content": "main theek hun tum kaise ho?"},
    ]
    assert filter_history_for_match(hist, "en") == []
