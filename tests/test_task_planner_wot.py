from src.runtime.cognitive_map import RuntimeAffordance
from src.runtime.task_planner import primitive_for_affordance


def test_writable_wot_property_is_an_invoke_not_a_read() -> None:
    affordance = RuntimeAffordance(
        id="wot_lights_brightness",
        source="wot",
        entity_id="lights",
        action_type="property",
        action_name="write_property",
        grounding={"thing_id": "lights", "href": "http://room/properties/brightness"},
        confidence=1.0,
    )

    assert primitive_for_affordance(affordance) == "invoke"
