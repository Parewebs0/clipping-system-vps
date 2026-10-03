"""Issue #27 — resolver: junk/tiny files, only videos count, folder priority, Whop uploads."""
from __future__ import annotations

import app.services.asset_resolve as ar

FOLDER = ar.FOLDER_MIME
MB = 1024 * 1024
BASE = ar.WHOP_PUBLIC_BASE_DEFAULT
REL = "organizations/org1/campaigns/references/videos/abc.mp4"


def _f(i, name, size=50 * MB, mime="video/mp4"):
    return {"id": i, "name": name, "mimeType": mime, "size": str(size)}


def _dir(i, name):
    return {"id": i, "name": name, "mimeType": FOLDER}


def _fake_tree(monkeypatch, tree):
    calls = []

    def ls(fid):
        calls.append(fid)
        return tree.get(fid, [])

    monkeypatch.setattr(ar, "gog_ls", ls)
    return calls


def test_junk_and_tiny_files_ignored(monkeypatch):
    _fake_tree(monkeypatch, {"root": [
        _f("a", "._ARZ x MANSORY.mov", size=4096),
        _f("b", ".DS_Store", size=6000, mime="application/octet-stream"),
        _f("c", "tiny.mp4", size=200 * 1024),
        _f("d", "real.mov"),
    ]})
    items = ar.walk_drive("root")
    assert [i["id"] for i in items] == ["d"]


def test_only_videos_count_towards_limit(monkeypatch):
    rows = [_f(f"img{i}", f"p{i}.jpg", mime="image/jpeg") for i in range(30)]
    rows += [_f(f"v{i}", f"v{i}.mp4") for i in range(15)]
    _fake_tree(monkeypatch, {"root": rows})
    items = ar.walk_drive("root")
    assert len(items) == ar.MAX_FILES
    assert all(i["id"].startswith("v") for i in items)


def test_folder_priority_clips_before_fonts(monkeypatch):
    tree = {
        "root": [_dir("music", "🎵 | MUSIQUES & POLICES"), _dir("logo", "Logos"), _dir("ext", "🗣️ | EXTRAITS YOMI")],
        "music": [_f(f"m{i}", f"song{i}.mp4") for i in range(12)],
        "logo": [_f(f"l{i}", f"logo{i}.mov") for i in range(12)],
        "ext": [_f(f"e{i}", f"extrait{i}.mp4") for i in range(12)],
    }
    calls = _fake_tree(monkeypatch, tree)
    items = ar.walk_drive("root")
    assert all(i["id"].startswith("e") for i in items)
    assert calls == ["root", "ext"]  # stops once 12 videos found


def test_depth_three_reached(monkeypatch):
    tree = {"root": [_dir("a", "x")], "a": [_dir("b", "y")], "b": [_dir("c", "clips")], "c": [_f("v", "v.mp4")]}
    _fake_tree(monkeypatch, tree)
    assert [i["id"] for i in ar.walk_drive("root")] == ["v"]


def test_folder_priority_scores():
    assert ar.folder_priority("RAW FOOTAGE") == 1
    assert ar.folder_priority("Fonts") == -1
    assert ar.folder_priority("Misc") == 0


def test_whop_relative_paths_normalized_and_classified():
    assert ar.normalize_url(REL) == BASE + REL
    assert ar.classify(REL) == "whop_video"
    assert ar.classify("organizations/o/campaigns/references/images/x.webp") == "whop_other"
    assert ar.normalize_url("random/path.mp4") == "random/path.mp4"


def test_whop_video_verified_with_head(monkeypatch):
    seen = []

    def head(url, timeout=15.0):
        seen.append(url)
        return True, 22 * MB, "video/mp4"

    monkeypatch.setattr(ar, "head_check", head)
    found, errors = ar.expand_all([REL, "organizations/o/campaigns/references/assets/a.zip"])
    assert errors == []
    assert seen == [BASE + REL]
    assert found[0]["source_url"] == BASE + REL
    assert found[0]["size"] == 22 * MB
    assert found[0]["source_provider"] == "direct"


def test_whop_video_unreachable_is_error(monkeypatch):
    monkeypatch.setattr(ar, "head_check", lambda url, timeout=15.0: (False, None, ""))
    found, errors = ar.expand_all([REL])
    assert found == []
    assert errors and errors[0].startswith("whop_asset_unreachable")


def test_whop_tiny_or_non_video_skipped(monkeypatch):
    monkeypatch.setattr(ar, "head_check", lambda url, timeout=15.0: (True, 1000, "video/mp4"))
    assert ar.expand_url(REL) == ([], None)
    monkeypatch.setattr(ar, "head_check", lambda url, timeout=15.0: (True, 9 * MB, "text/html"))
    assert ar.expand_url(REL) == ([], None)


def test_only_whop_images_is_not_an_unsupported_error():
    found, errors = ar.expand_all(["organizations/o/campaigns/references/images/x.webp"])
    assert found == [] and errors == ["no_ingestible_urls"]
