import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import synth
from funkbox.reel import approval, ingest, project as P
from funkbox.reel.schema import ReelError
from funkbox.reel.validate import validate

ROOT = Path(__file__).resolve().parents[2]
LINES = {"hook": {"text": "LOOK CLOSER.\nTIME FOR A CLEAN?", "say": "Look closer. Time for a clean?"},
         "body1": {"text": "TOUGH ON STAINS."}, "url": {"text": "GLOWMOP.EXAMPLE"}}


@pytest.fixture
def penv(work, monkeypatch):
    monkeypatch.setenv("FUNK_PROJECTS", str(work / "projects"))
    monkeypatch.setenv("FUNK_INBOX", str(work / "inbox"))
    P.new_project("demo")
    return work


def write_reel(slug="demo", copy="copy.json", cid="hook"):
    d = P.project_dir(slug)
    reel = {"version": 3, "settings": {"width": 270, "height": 480, "fps": 10},
            "brand": {"primary": "#c8102e", "secondary": "#1a1a1a", "text": "#ffffff"},
            "copy": copy,
            "shots": [{"id": "s1", "source": {"color": "#202020"}, "duration": 1.0,
                       "caption": {"copy_id": cid, "style": "red-box", "reveal": "none"}}]}
    p = d / f"{slug}.reel.json"
    p.write_text(json.dumps(reel))
    return p


def propose(lines=None, **kw):
    return approval.propose_copy("demo", lines or LINES, **kw)


# ------------------------------------------------------------------ propose
def test_propose_writes_only_a_draft_and_returns_the_exact_listing(penv):
    r = propose(pronounce={"GLOWMOP": "glow mop"})
    d = P.project_dir("demo")
    assert (d / "copy.draft.json").exists() and not (d / "copy.json").exists()
    assert r["draft_sha256"] == P.sha256_file(d / "copy.draft.json")
    txt = approval.propose_text(r, "demo")
    assert '1. hook' in txt and '"LOOK CLOSER.\\nTIME FOR A CLEAN?"' in txt and "2. body1" in txt
    assert 'say:  "Look closer. Time for a clean?"' in txt and "VERBATIM" in txt and r["draft_sha256"] in txt
    assert P.load_meta("demo")["copy_lock"] is None


@pytest.mark.parametrize("lines,frag", [
    ({"Hook": {"text": "A"}}, "must match"),
    ({"1hook": {"text": "A"}}, "must match"),
    ({"a" * 40: {"text": "A"}}, "must match"),
    ({"hook": {"text": "x" * 91}}, "91 characters"),
    ({"hook": {"text": " padded"}}, "whitespace"),
    ({"hook": "bare string"}, "bare string"),
    ({"hook": {"text": "A", "color": "red"}}, "only"),
    ({"hook": {"text": ""}}, "non-empty"),
    ({"hook": {"text": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}}, "does not fit"),
    ({}, "non-empty"),
])
def test_propose_rejects_bad_drafts_and_writes_nothing(penv, lines, frag):
    with pytest.raises(ReelError) as e:
        approval.propose_copy("demo", lines)
    assert frag in str(e.value)
    assert not (P.project_dir("demo") / "copy.draft.json").exists()


# ------------------------------------------------------------------ approve + lock
def test_approve_requires_the_current_hash_and_writes_the_drafts_exact_bytes(penv):
    r = propose()
    d = P.project_dir("demo")
    with pytest.raises(approval.ApprovalError, match="stale"):
        approval.approve("demo", "0" * 64, "chat")
    assert not (d / "copy.json").exists()
    # the draft changes after the user saw the first listing -> the old hash is rejected
    r2 = propose({"hook": {"text": "SOMETHING ELSE"}})
    assert r2["draft_sha256"] != r["draft_sha256"]
    with pytest.raises(approval.ApprovalError, match="stale"):
        approval.prepare_approval("demo", r["draft_sha256"])
    with pytest.raises(approval.ApprovalError, match="stale"):
        approval.approve("demo", r["draft_sha256"], "elicitation")
    assert not (d / "copy.json").exists() and P.load_meta("demo")["copy_lock"] is None
    out = approval.approve("demo", r2["draft_sha256"], "elicitation")
    assert (d / "copy.json").read_bytes() == (d / "copy.draft.json").read_bytes()
    m = P.load_meta("demo")
    assert m["copy_lock"] == P.sha256_file(d / "copy.json") == out["copy_lock"]
    assert m["copy_approval"]["method"] == "elicitation" and m["copy_approval"]["at"].endswith("Z")
    assert m["status"] == "copy-approved"
    with pytest.raises(approval.ApprovalError):
        approval.approve("demo", r2["draft_sha256"], "yolo")


def test_editing_copy_json_after_approval_makes_validate_and_render_fail(penv):
    r = propose()
    approval.approve("demo", r["draft_sha256"], "chat")
    reel = write_reel()
    ok = validate(str(reel))
    assert ok.ok, ok.text()
    cj = P.project_dir("demo") / "copy.json"
    cj.write_text(cj.read_text(encoding="utf-8").replace("LOOK CLOSER.", "LOOK CLOSER!"), encoding="utf-8")
    bad = validate(str(reel))
    assert not bad.ok
    assert "copy.json changed since approval; run propose_copy/approve_copy again." in bad.text()
    from funkbox.reel import render
    with pytest.raises(ReelError, match="changed since approval"):
        render.prepare(str(reel))
    with pytest.raises(ReelError, match="changed since approval"):
        render.render_reel(str(reel), "x", out_dir=penv / "o", draft=True)
    # dict reels (the MCP path) are held to the same lock
    d = json.loads(reel.read_text())
    d["copy"] = "projects/demo/copy.json" if False else str(cj)
    assert not validate(d).ok and "changed since approval" in validate(d).text()
    # a person re-locks it (CLI path), after which it validates again
    approval.relock("demo")
    assert validate(str(reel)).ok


def test_unapproved_project_copy_cannot_be_used(penv):
    propose()
    reel = write_reel()
    rep = validate(str(reel))
    assert not rep.ok                                                    # no approved copy.json at all
    d = P.project_dir("demo")
    (d / "copy.json").write_bytes((d / "copy.draft.json").read_bytes())   # hand-placed, never approved
    assert "has not been approved" in validate(str(reel)).text()


def test_the_draft_can_never_reach_pixels(penv):
    r = propose()
    approval.approve("demo", r["draft_sha256"], "chat")
    propose({"hook": {"text": "UNAPPROVED LINE"}})
    reel = write_reel(copy="copy.draft.json", cid="hook")
    rep = validate(str(reel))
    assert not rep.ok and "only the approved copy.json" in rep.text()
    # a dict reel without a base dir, pointing into the project, is refused the same way
    d = json.loads(reel.read_text())
    d["copy"] = str(P.project_dir("demo") / "copy.draft.json")
    assert "only the approved copy.json" in validate(d).text()


def test_project_reels_cannot_borrow_another_copy_deck(penv):
    r = propose()
    approval.approve("demo", r["draft_sha256"], "chat")
    other = penv / "other_copy.json"
    other.write_text(json.dumps({"lines": {"hook": {"text": "SNEAKY"}}}))
    reel = write_reel(copy=str(other))
    rep = validate(str(reel))
    assert not rep.ok and "not that project's copy.json" in rep.text()
    # and a second project's approved deck is not usable from this project's reel
    P.new_project("second")
    r2 = approval.propose_copy("second", {"hook": {"text": "SECOND"}})
    approval.approve("second", r2["draft_sha256"], "chat")
    reel = write_reel(copy=str(P.project_dir("second") / "copy.json"))
    assert "uses the copy deck of project" in validate(str(reel)).text()


def test_copy_diff(penv):
    r = propose()
    assert "NEW" in approval.copy_diff("demo") and "none yet" in approval.copy_diff("demo")
    approval.approve("demo", r["draft_sha256"], "chat")
    propose({"hook": {"text": "LOOK CLOSER.\nTIME FOR A CLEAN?", "say": "Look closer. Time for a clean?"},
             "body1": {"text": "TOUGH ON DIRT."}, "extra": {"text": "NEW ONE"}})
    t = approval.copy_diff("demo")
    assert "= hook: unchanged" in t and "~ body1: CHANGED" in t and "+ extra: NEW" in t and "- url: REMOVED" in t
    assert "LOCKED and intact" in t and "NOT in effect" in t


# ------------------------------------------------------------------ CLI (the person's own path)
def run_cli(penv, *args, stdin=""):
    env = {**os.environ, "FUNK_PROJECTS": str(penv / "projects"), "FUNK_INBOX": str(penv / "inbox")}
    return subprocess.run([sys.executable, str(ROOT / "reel_cli.py"), *args], input=stdin, capture_output=True,
                          text=True, env=env, cwd=penv)


def test_cli_approve_copy_relocks_after_a_hand_edit(penv):
    r = propose()
    approval.approve("demo", r["draft_sha256"], "chat")
    reel = write_reel()
    cj = P.project_dir("demo") / "copy.json"
    cj.write_text(cj.read_text(encoding="utf-8").replace("STAINS", "MARKS"), encoding="utf-8")
    assert not validate(str(reel)).ok
    p = run_cli(penv, "approve-copy", "demo", "--stdin", stdin="n\n")
    assert p.returncode == 1 and "not approved" in p.stdout and not validate(str(reel)).ok
    p = run_cli(penv, "approve-copy", "demo", "--stdin", stdin="y\n")
    assert p.returncode == 0, p.stderr
    assert "TOUGH ON MARKS." in p.stdout                                  # the user sees the real current text
    assert validate(str(reel)).ok
    m = P.load_meta("demo")
    assert m["copy_approval"]["method"] == "cli" and m["copy_lock"] == P.sha256_file(cj)
    # without --stdin and without a terminal it refuses outright
    p = run_cli(penv, "approve-copy", "demo", stdin="y\n")
    assert p.returncode == 1 and "person at a terminal" in p.stderr


def test_cli_approve_from_draft(penv):
    propose()
    p = run_cli(penv, "approve-copy", "demo", "--from-draft", "--stdin", stdin="y\n")
    assert p.returncode == 0, p.stderr
    assert (P.project_dir("demo") / "copy.json").exists() and P.copy_lock_error("demo") is None


# ------------------------------------------------------------------ MCP stdio: elicitation
mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters, types  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def _session(penv, fn, elicit=None):
    env = {**os.environ, "FUNK_OUT": str(penv / "out"), "FUNK_PROJECTS": str(penv / "projects"),
           "FUNK_INBOX": str(penv / "inbox"), "REEL_DEPTH": "stub", "REEL_TTS": "stub"}
    env.setdefault("FUNK_GL_BACKEND", "egl")
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "reel_mcp.py")], env=env)
    async with stdio_client(params) as (r, w):
        kw = {"elicitation_callback": elicit} if elicit else {}
        async with ClientSession(r, w, **kw) as s:
            await s.initialize()
            return await fn(s)


def txt(res):
    return "\n".join(c.text for c in res.content if c.type == "text")


def make_cb(decision, seen):
    async def cb(context, params):
        seen.append(params.message)
        if decision == "decline":
            return types.ElicitResult(action="decline")
        return types.ElicitResult(action="accept", content={"decision": decision})
    return cb


def test_mcp_elicitation_accept_approves_and_the_user_sees_the_exact_listing(penv):
    seen = []

    async def go(s):
        p = await s.call_tool("propose_copy", {"project": "demo", "lines": LINES})
        sha = txt(p).split("draft_sha256: ")[1].split()[0]
        # a stale/wrong hash never reaches the prompt
        bad = await s.call_tool("approve_copy", {"project": "demo", "draft_sha256": "0" * 64})
        assert "stale" in txt(bad) and not seen
        a = await s.call_tool("approve_copy", {"project": "demo", "draft_sha256": sha})
        return txt(a)
    out = asyncio.run(_session(penv, go, make_cb("approve", seen)))
    assert out.startswith("APPROVED (elicitation)"), out
    assert len(seen) == 1 and '"LOOK CLOSER.\\nTIME FOR A CLEAN?"' in seen[0] and "3. url" in seen[0]
    assert P.copy_lock_error("demo") is None
    assert P.load_meta("demo")["copy_approval"]["method"] == "elicitation"


@pytest.mark.parametrize("cb_kind", ["reject", "decline"])
def test_mcp_elicitation_reject_blocks_even_if_the_agent_claims_chat_confirmation(penv, cb_kind):
    seen = []

    async def go(s):
        p = await s.call_tool("propose_copy", {"project": "demo", "lines": LINES})
        sha = txt(p).split("draft_sha256: ")[1].split()[0]
        a = await s.call_tool("approve_copy", {"project": "demo", "draft_sha256": sha})
        b = await s.call_tool("approve_copy", {"project": "demo", "draft_sha256": sha,
                                               "user_confirmed_in_chat": True})
        return txt(a), txt(b)
    a, b = asyncio.run(_session(penv, go, make_cb(cb_kind, seen)))
    assert a.startswith("NOT approved") and b.startswith("NOT approved"), (a, b)
    assert len(seen) == 2                                              # the flag did not skip the prompt
    assert not (P.project_dir("demo") / "copy.json").exists() and P.load_meta("demo")["copy_lock"] is None


def test_mcp_fallback_needs_user_confirmed_in_chat(penv):
    async def go(s):
        p = await s.call_tool("propose_copy", {"project": "demo", "lines": LINES})
        sha = txt(p).split("draft_sha256: ")[1].split()[0]
        a = await s.call_tool("approve_copy", {"project": "demo", "draft_sha256": sha})
        assert not (P.project_dir("demo") / "copy.json").exists()
        a2 = await s.call_tool("approve_copy", {"project": "demo", "draft_sha256": sha,
                                                "user_confirmed_in_chat": False})
        stale = await s.call_tool("approve_copy", {"project": "demo", "draft_sha256": "f" * 64,
                                                   "user_confirmed_in_chat": True})
        b = await s.call_tool("approve_copy", {"project": "demo", "draft_sha256": sha,
                                               "user_confirmed_in_chat": True})
        tools = {t.name: t.description for t in (await s.list_tools()).tools}
        return txt(a), txt(a2), txt(stale), txt(b), tools
    a, a2, stale, b, tools = asyncio.run(_session(penv, go))                 # no elicitation callback
    assert a.startswith("NOT approved") and "user_confirmed_in_chat=true" in a and '"LOOK CLOSER' in a
    assert a2.startswith("NOT approved") and "stale" in stale
    assert b.startswith("APPROVED (chat)"), b
    assert P.load_meta("demo")["copy_approval"]["method"] == "chat"
    assert "only set this after the user explicitly approved the exact listing in this conversation" in " ".join(tools["approve_copy"].split())
