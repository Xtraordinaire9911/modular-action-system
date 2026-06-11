from src.skill_library import load_skill_library


def test_load_seed_skill_library():
    library = load_skill_library("config/skills_seed.json")

    assert "set_temperature" in library.ids()
    skill = library.get("set_temperature")
    assert skill.preferred_backends == ["wot"]
    assert "dom" in skill.allowed_backends
    assert skill.rollback is not None
