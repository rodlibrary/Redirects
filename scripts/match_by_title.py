#!/usr/bin/env python3
"""Fill preservica_ref in the manifest by matching ScholarWorks titles to Preservica.

For when the migrated Assets don't carry dc_article identifiers. The script:

  1. Lists every Folder and Asset under --root-folder (default: the whole
     repository) through the Entity API, with titles and references, and saves
     that inventory to staging/preservica_assets.csv. Rerun with
     --reuse-inventory to skip this step.
  2. Matches each manifest row that has no preservica_ref to an Asset with the
     same title (titles from staging/dc_items.csv; case, punctuation, accents
     and spacing are ignored):
       * one Asset with that title                -> matched ("title")
       * several, but only one in a folder where
         this series' other items were found     -> matched ("title + folder")
       * otherwise                                -> left for review
     Each Asset is used for one item at most; if two items claim the same Asset,
     both go to review.
  3. Matches collection rows (etd, pias, pias/vol94/iss4) to the deepest Folder
     holding at least --folder-share of that collection's matched items.

Rows that already have a preservica_ref, including ones typed in by hand, are
never changed. Everything not matched is listed with its candidates in
staging/title_match_review.csv; fill those in by hand (paste the reference or
portal address into preservica_ref) and they're kept on later runs.

Credentials as for match_preservica.py: PRESERVICA_HOST, PRESERVICA_USER,
PRESERVICA_PASSWORD and PRESERVICA_TENANT (UNIOWA for Rod Library). The account
needs Read Metadata on the migrated records' security tags; the script stops
after logging in if the account can't see any records.

Usage:
    python scripts/match_by_title.py --manifest manifest/redirects.csv \\
        --items staging/dc_items.csv [--root-folder SO_<uuid>]
"""

from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import COLUMNS, PRESERVICA_REF_RE, build_target, normalize_ref, read_manifest  # noqa: E402
from match_preservica import Preservica, local  # noqa: E402

INVENTORY_FIELDS = ["type", "ref", "title", "parent_ref"]


def norm_title(title: str) -> str:
    """Comparable form of a title: entities decoded, accents and punctuation dropped."""
    t = html.unescape(title or "")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.casefold()
    t = re.sub(r"[^\w]+", " ", t)
    return " ".join(t.split())


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def walk(api: Preservica, root: str, page_size: int) -> list[dict]:
    """List all Folders and Assets under root ("" = repository root), breadth first."""
    found: list[dict] = []
    queue: list[str] = [root]
    folders_done = 0
    started = time.time()
    while queue:
        folder = queue.pop(0)
        path = f"/api/entity/structural-objects/{folder}/children" if folder else "/api/entity/root/children"
        resp = api.get(path, {"start": 0, "max": page_size})
        while True:
            next_url = ""
            for el in resp.iter():
                name = local(el.tag)
                if name == "Child":
                    etype = el.get("type", "")
                    entry = {"type": etype, "ref": el.get("ref", ""),
                             "title": el.get("title", ""), "parent_ref": folder}
                    found.append(entry)
                    if etype == "SO":
                        queue.append(entry["ref"])
                elif name == "Next" and (el.text or "").strip():
                    next_url = el.text.strip()
            if not next_url:
                break
            resp = api.get_url(next_url)
        folders_done += 1
        if folders_done % 50 == 0:
            n_io = sum(1 for e in found if e["type"] == "IO")
            print(f"  {folders_done} folders read, {n_io} assets so far "
                  f"({time.time() - started:.0f}s)", file=sys.stderr)
    return found


def read_inventory(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_inventory(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=INVENTORY_FIELDS)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def load_titles(items_csv: Path) -> dict[str, str]:
    """Title for each item, keyed by both "context/article" and dc_path."""
    titles: dict[str, str] = {}
    with items_csv.open(newline="", encoding="utf-8-sig") as fh:
        for item in csv.DictReader(fh):
            title = (item.get("title") or "").strip()
            context = (item.get("context") or "").lower()
            article = item.get("article") or ""
            if context and article:
                titles[f"{context}/{article}"] = title
            if item.get("dc_path"):
                titles[item["dc_path"].lower()] = title
    return titles


def series_of(row: dict) -> str:
    key = row["dc_article"] or row["dc_path"]
    return key.split("/")[0]


def is_collection(dc_path: str, dc_article: str) -> bool:
    """etd, pias, pias/vol94, pias/vol94/iss4 - but not etd/123 or pias/vol94/iss4/3."""
    if dc_article or not dc_path:
        return False
    parts = dc_path.split("/")
    if len(parts) == 1:
        return True
    return parts[-1].startswith(("vol", "iss")) and not parts[-1].isdigit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="manifest/redirects.csv")
    ap.add_argument("--items", default="staging/dc_items.csv", help="harvest with titles")
    ap.add_argument("--out", help="where to write the manifest (default: overwrite --manifest)")
    ap.add_argument("--inventory", default="staging/preservica_assets.csv")
    ap.add_argument("--reuse-inventory", action="store_true",
                    help="use the saved inventory instead of listing Preservica again")
    ap.add_argument("--root-folder", default=os.environ.get("PRESERVICA_ROOT_FOLDER", ""),
                    help="Folder to list (SO_<uuid>, UUID or portal address); default: whole repository")
    ap.add_argument("--review", default="staging/title_match_review.csv")
    ap.add_argument("--folder-share", type=float, default=0.9,
                    help="share of a collection's matched items a Folder must hold to stand for it")
    ap.add_argument("--min-folder-items", type=int, default=3)
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--pause", type=float, default=0.05)
    args = ap.parse_args()

    manifest_path = Path(args.manifest)
    out_path = Path(args.out or args.manifest)
    inventory_path = Path(args.inventory)

    # 1. Inventory
    if args.reuse_inventory and inventory_path.exists():
        inventory = read_inventory(inventory_path)
        print(f"using saved inventory {inventory_path} ({len(inventory)} entries)")
    else:
        env = {k: os.environ.get(k, "") for k in
               ("PRESERVICA_HOST", "PRESERVICA_USER", "PRESERVICA_PASSWORD", "PRESERVICA_TENANT")}
        missing = [k for k, v in env.items() if not v and k != "PRESERVICA_TENANT"]
        if missing:
            print(f"error: set {', '.join(missing)}", file=sys.stderr)
            return 1
        root = ""
        if args.root_folder:
            ref = normalize_ref(args.root_folder)
            if not PRESERVICA_REF_RE.match(ref):
                print(f"error: --root-folder '{args.root_folder}' isn't a Preservica reference", file=sys.stderr)
                return 1
            root = ref.split("_", 1)[1]
        api = Preservica(env["PRESERVICA_HOST"], env["PRESERVICA_USER"], env["PRESERVICA_PASSWORD"],
                         env["PRESERVICA_TENANT"], args.pause)
        print(f"listing Preservica {'folder ' + root if root else 'repository'} ...")
        inventory = walk(api, root, args.page_size)
        write_inventory(inventory_path, inventory)
        print(f"saved {len(inventory)} entries to {inventory_path}")

    folders = {e["ref"]: e for e in inventory if e["type"] == "SO"}
    assets = [e for e in inventory if e["type"] == "IO"]
    by_title: dict[str, list[dict]] = defaultdict(list)
    for a in assets:
        by_title[norm_title(a["title"])].append(a)

    def ancestors(folder_ref: str) -> list[str]:
        """The folder and its parents, nearest first."""
        chain = []
        while folder_ref and folder_ref not in chain:
            chain.append(folder_ref)
            folder_ref = folders.get(folder_ref, {}).get("parent_ref", "")
        return chain

    def folder_label(ref: str) -> str:
        names = [folders[f]["title"] for f in reversed(ancestors(ref)) if f in folders]
        return " / ".join(names) or "(top level)"

    # 2. Items
    titles = load_titles(Path(args.items))
    rows = [dict(r.data) for r in read_manifest(manifest_path)]
    todo = [r for r in rows if not r["preservica_ref"] and r["status"] != "withdrawn"
            and not is_collection(r["dc_path"], r["dc_article"])]

    claims: dict[str, list[dict]] = defaultdict(list)   # asset ref -> rows that want it
    choice: dict[int, tuple[dict, str]] = {}             # id(row) -> (asset, method)
    review: list[dict] = []
    ambiguous: list[tuple[dict, list[dict]]] = []

    def add_review(row, title, reason, cands=()):
        row["notes"] = f"review: {reason}"
        review.append({
            "dc_path": row["dc_path"], "dc_article": row["dc_article"], "title": title,
            "reason": reason,
            "candidates": "; ".join(f"IO_{c['ref']} [{folder_label(c['parent_ref'])}]" for c in list(cands)[:5]),
        })

    # Pass 1: unique titles
    for row in todo:
        title = titles.get(row["dc_article"]) or titles.get(row["dc_path"]) or ""
        row["_title"] = title
        if not title:
            add_review(row, "", "no title in the harvest")
            continue
        cands = by_title.get(norm_title(title), [])
        if not cands:
            add_review(row, title, "no Preservica asset with this title")
        elif len(cands) == 1:
            choice[id(row)] = (cands[0], "title")
            claims[cands[0]["ref"]].append(row)
        else:
            ambiguous.append((row, cands))

    # Folders where each series' uniquely matched items live
    series_folders: dict[str, Counter] = defaultdict(Counter)
    for row in todo:
        if id(row) in choice:
            series_folders[series_of(row)][choice[id(row)][0]["parent_ref"]] += 1

    # Pass 2: duplicate titles, narrowed by folder
    for row, cands in ambiguous:
        known = series_folders.get(series_of(row), Counter())
        narrowed = [c for c in cands if c["parent_ref"] in known]
        if len(narrowed) == 1:
            choice[id(row)] = (narrowed[0], "title + folder")
            claims[narrowed[0]["ref"]].append(row)
        else:
            reason = (f"{len(cands)} assets share this title" if not narrowed else
                      f"{len(narrowed)} assets with this title in this series' folders")
            add_review(row, row["_title"], reason, narrowed or cands)

    # An asset claimed by two items can't be right for both
    for ref, claimants in claims.items():
        if len(claimants) > 1:
            for row in claimants:
                asset, _ = choice.pop(id(row))
                add_review(row, row["_title"], f"same asset as {len(claimants) - 1} other item(s)", [asset])

    matched = Counter()
    for row in todo:
        if id(row) in choice:
            asset, method = choice[id(row)]
            ref = f"IO_{asset['ref']}"
            row.update(preservica_ref=ref, target_url=build_target(ref), status="active",
                       notes=f"{method}: {folder_label(asset['parent_ref'])}")
            matched[method] += 1

    # 3. Collections: deepest Folder holding most of the collection's matched items.
    # Index each matched item's folder under every collection path above it.
    parent_of = {f"IO_{a['ref']}": a["parent_ref"] for a in assets}
    by_collection: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        ref, path = row["preservica_ref"], row["dc_path"]
        if ref in parent_of and path and not is_collection(path, row["dc_article"]):
            parts = path.split("/")
            for i in range(1, len(parts)):
                by_collection["/".join(parts[:i])].append(parent_of[ref])
    collections_done = 0
    chosen: dict[str, str] = {}   # collection path -> folder uuid (hand-entered or matched)
    for row in rows:
        if row["preservica_ref"].startswith("SO_") and is_collection(row["dc_path"], row["dc_article"]):
            chosen[row["dc_path"]] = row["preservica_ref"][3:]
    # Deepest collections first, so an issue is placed before its volume and journal;
    # a collection's Folder must then sit above every sub-collection's Folder.
    for row in sorted(rows, key=lambda r: -r["dc_path"].count("/")):
        if row["preservica_ref"] or not is_collection(row["dc_path"], row["dc_article"]):
            continue
        path = row["dc_path"]
        below = [fo for p, fo in chosen.items() if p.startswith(path + "/")]
        members = by_collection.get(path, [])
        if len(members) < args.min_folder_items:
            add_review(row, "", f"collection: only {len(members)} matched item(s) to locate its folder")
            continue
        counts = Counter()
        for parent in members:
            for f in ancestors(parent):
                counts[f] += 1
        need = args.folder_share * len(members)
        good = [f for f, n in counts.items() if n >= need
                and all(f in ancestors(b)[1:] for b in below)]
        if not good:
            add_review(row, "", f"collection: no single folder holds its {len(members)} matched items"
                                + (" above its sub-collections" if below else ""))
            continue
        best = max(good, key=lambda f: len(ancestors(f)))  # deepest qualifying folder
        ref = f"SO_{best}"
        row.update(preservica_ref=ref, target_url=build_target(ref), status="active",
                   notes=f"folder holding {counts[best]} of {len(members)} matched items: {folder_label(best)}")
        chosen[path] = best
        collections_done += 1

    # Write results
    for row in rows:
        row.pop("_title", None)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    review_path = Path(args.review)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    with review_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["dc_path", "dc_article", "title", "reason", "candidates"])
        w.writeheader()
        w.writerows(review)

    total = len(todo)
    print(f"{total} items without a reference: {matched['title']} matched by title, "
          f"{matched['title + folder']} by title + folder, "
          f"{total - sum(matched.values())} for review")
    print(f"{collections_done} collection folder(s) matched")
    print(f"wrote {out_path}; review list in {review_path} ({len(review)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
