from agentos_cli import skills as S


def sk(body="hello", sid="demo"):
    return S.Skill("demo", "a demo", "workspace", sid, {"SKILL.md": f"---\nname: demo\n---\n{body}\n".encode()})


def test_bundled_skills_ship_with_the_cli():
    names = {s.name for s in S.bundled()}
    assert {"aos", "agentos-setup", "agentos-crm", "agentos-meetings", "agentos-tasks", "agentos-workspace"} == names
    for s in S.bundled():
        assert s.files["SKILL.md"].startswith(b"---\nname: ")


def test_install_update_current_and_remove(home):
    base = home / ".claude" / "skills"
    assert S.install(sk(), base) == ("new", base / "demo")
    assert (base / "demo" / "SKILL.md").read_text().endswith("hello\n")
    assert S.plan_install(sk(), base)[0] == "current"
    assert S.install(sk("v2"), base)[0] == "update"
    assert S.remove(base, "demo") == "removed"
    assert not (base / "demo").exists()


def test_never_touches_a_folder_that_isnt_ours(home):
    base = home / ".claude" / "skills"
    (base / "demo").mkdir(parents=True)
    (base / "demo" / "SKILL.md").write_text("mine")
    st, dest = S.install(sk(), base)
    assert st == "new" and dest.name == "agentos-demo"
    assert (base / "demo" / "SKILL.md").read_text() == "mine"
    assert S.remove(base, "demo") == "removed"           # removes agentos-demo only
    assert (base / "demo" / "SKILL.md").read_text() == "mine"
    assert S.remove(base, "demo") == "not-ours"


def test_edited_skill_is_left_alone(home):
    base = home / ".agents" / "skills"
    S.install(sk(), base)
    (base / "demo" / "SKILL.md").write_text("my tweak")
    assert S.install(sk("v2"), base)[0] == "edited"
    assert (base / "demo" / "SKILL.md").read_text() == "my tweak"
    assert S.remove(base, "demo") == "edited"
    assert S.install(sk("v2"), base, force=True)[0] == "update"


def test_frontmatter_for_workspace_skills():
    class C:
        def call(self, tool, name, arguments):
            assert tool == "workspace_call" and name == "skills_get"
            return {"name": "Gmail full email body", "content": "# Body\n", "tags": []}
    s = S.workspace_skill(C(), "gmail-full-email-body", {"description": 'Read "whole" bodies'})
    text = s.files["SKILL.md"].decode()
    assert text.startswith("---\nname: gmail-full-email-body\ndescription: \"Read 'whole' bodies\"\n---\n\n# Body")


def test_target_dirs_dedupe_shared_folder(home):
    dirs = dict(S.target_dirs(["claude-code", "codex", "opencode"]))
    assert dirs[home / ".claude" / "skills"] == ["Claude Code"]
    assert dirs[home / ".agents" / "skills"] == ["Codex", "opencode"]
