#!/usr/bin/env python3
"""Build the GitHub Pages site (docs/) from examples/, references/ and scripts/ugc_script.py. Stdlib only.

    python3 scripts/site.py          # writes docs/
    python3 scripts/site.py --check  # exits 1 if docs/ is out of date

Lint rules, stats and prompts come from ugc_script.py, so the site, the CLI and the committed examples cannot
disagree. The in-browser checker (site/checker.js) is a port of that code; tests/test_site.py runs both over every
example and the failing one and fails on any difference. Static assets live in site/ and are copied as is.
"""
import copy
import html
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ugc_script as us  # noqa: E402

ROOT = us.ROOT
OUT = os.path.join(ROOT, "docs")
STATIC = os.path.join(ROOT, "site")

BASE = "https://uniqaiapp.github.io/ugc-ad-director/"
REPO = "https://github.com/uniqaiapp/ugc-ad-director"
HUB = "https://uniqaiapp.github.io/"
UNIQ = "https://" + "uniqai.app"
CAMPAIGN = "ugc-ad-director"
SITE_UPDATED = "2026-11-26"  # fixed so the output is deterministic; bump when the site content changes
RULES_CHECKED = "2026-10-28"  # the date at the top of references/compliance.md
OG_IMAGE = BASE + "assets/social-preview.png"
OG_SIZE = (1707, 853)
POSTHOG_KEY = "phc_AVmhcac2jVRwi9asHEuhzJXnAbQA2hzfUxzWvzjyLPc8"  # public project key (EU), same as uniqai.app
LINKEDIN = "https://www.linkedin.com/in/vlad-yasinski-ua5er/"
AUTHOR = {"@type": "Person", "name": "Vlad Yasinski", "url": "https://www.roimobi.com/",
          "sameAs": ["https://www.roimobi.com", LINKEDIN, "https://github.com/ua5er"]}
BAD_SLUG = "fake-testimonial"
COMPLIANCE_URL = REPO + "/blob/main/references/compliance.md"

TOOLS = [  # other uniqaiapp tools, shown in "More free tools"
    ("awesome-ai-video-prompts", "Prompt gallery", "AI video prompt gallery with model settings."),
    ("video-director", "Storyboard builder", "Timed beats in, paste-ready AI video prompts out for Kling, Veo and others."),
    ("no-plastic", "Natural AI portraits", "Prompt rules and a checker that keep AI portraits from looking plastic."),
    ("ai-video-pricing", "AI video prices", "Price per second and per clip for AI video models, with a source and date on every row."),
    ("deslop-ru", "Russian AI-text cleaner", "Find and rewrite the phrases that make Russian text sound machine-written."),
    ("suno-producer", "Suno song producer", "Structure, tags and style lines for Suno songs."),
]

PRESENTER_LABEL = {"ai_generated": "AI presenter", "real_person": "real person", "none": "no presenter"}
HOOK_LABEL = {"test": "test", "pov": "POV", "pattern-interrupt": "pattern interrupt", "result-first": "result first"}
PLATFORM_LABEL = {"tiktok": "TikTok", "reels": "Reels", "shorts": "YouTube Shorts"}
TAG_NAMES = {"PI": "pattern interrupt", "CO": "callout", "CT": "contrarian", "RF": "result first", "CU": "curiosity",
             "CM": "comparison", "OB": "objection", "POV": "POV", "TEST": "a check the viewer can do now"}
MODEL_LABEL = {"image": "Image", "kling": "Kling", "veo3": "Veo 3"}
MODEL_FULL = {"image": "First-frame image prompts", "kling": "Kling video prompts", "veo3": "Veo 3 video prompts (with speech)"}

# Plain-language reason for each lint message, matched in order (first match wins). The same regexes run in
# checker.js. Every sentence is backed by references/compliance.md, structure.md or script-format.md.
RULES = [
    (r"ai_label is empty", "Generated footage needs a plain on-screen label from the first frame (compliance.md, section 1)."),
    (r"ai_label '.*' does not appear in any caption", "The label counts only if it sits in a caption. Platform buttons can cover a corner, so a caption line is safest."),
    (r"platform_toggle", "Switch on the platform's AI-content setting when you upload the ad (compliance.md, section 1)."),
    (r"describe the AI presenter", "The description must be of a fictional person, not modelled on anyone real (compliance.md, section 3)."),
    (r"first-person customer testimonial", "An AI presenter is a demonstrator, not a customer. A fake testimonial breaks the FTC rule in 16 CFR Part 465 (compliance.md, section 2)."),
    (r"implies a platform or third-party endorsement", "Endorsement wording can mislead. Do not imply that a platform or another party backs the ad (compliance.md, section 3)."),
    (r"has no evidence|is a claim;|is not backed by an entry", "Anything measurable goes in claims, with where it is proven: a test report, the label or a dated spec sheet (compliance.md, section 4)."),
    (r"hook is \d+ words|hook shot ends at|first shot must be the hook|first shot must start at 0|hook text does not appear",
     "The hook has to land in the first 2 seconds, in 12 words or fewer, said and captioned together (structure.md)."),
    (r"caption line is", "Captions run up to 48 characters a line and two lines at most, so they stay readable on a phone (structure.md)."),
    (r"voiceover runs at", "The pace ceiling is 3.5 words per second. It was chosen for this repo and is not a researched optimum (structure.md)."),
    (r"missing beat|beats out of order|last shot must be the cta", "Every script uses five beats in one order: hook, problem, demo, proof, cta (structure.md)."),
    (r"starts at .* but previous|end_s must be greater|shots end at .* but duration_s", "The timeline must run without gaps and end on duration_s (script-format.md)."),
    (r"missing top-level field|missing field\(s\)|type must be one of|source must be one of", "A required field or value is missing. script-format.md lists them all."),
    (r"prompt is missing|AI-generated shot has no 'prompt'", "A generated shot needs a prompt with a subject, an action and a camera (model-prompts.md)."),
    (r"over 60 s", "Platform length limits change, so re-check the current ones before you run the ad."),
    (r"^no shots|hook text is empty|cta text is empty", "The script needs shots, a hook text and a call to action."),
]

INTROS = {
    "skincare-serum": "A fictional presenter holds up a flaky makeup sponge and talks to the camera as a demonstrator, never as a customer. The split screen in shot 4 shows how the serum goes on, so it works as an application demo and not as a result claim. The AI label is part of the proof, not something to hide.",
    "meal-prep-app": "Software gets real screen recordings here, because a generated app screen would show features that do not exist. Only the kitchen b-roll around the recordings is generated, and no presenter appears. The hook leads with the result: seven dinners and one grocery list.",
    "kitchen-chopper": "An overhead camera follows one onion into the chopper. The dishwasher footage in the proof beat is real, and the four seconds in the title is never timed or claimed in the ad itself. The only gadget claim, that three parts go on the top rack, sits in the claims list.",
    "dog-dental-chews": "This is the one script where a real owner speaks in the first person, because there is a signed release and proof of purchase behind it. The paid partnership is disclosed in speech and on screen. Only shot 4 is generated and its label says b-roll, and there is no vet recommendation and no plaque percentage.",
    "language-course": "The hook is contrarian and the script runs 45 seconds, the longest of the eleven. The only numbers are counts from the product itself, such as thirty situations. The fictional presenter never says he learned Spanish with the course.",
    "b2b-invoicing": "An objection hook opens a script about reminders that go out on days seven, fourteen and twenty-one, and that schedule is listed as a claim with evidence. The reminder flow is a real screen recording, while the paid notification is generated. Nothing in it promises faster payment.",
    "cleaning-spray": "At 15 seconds this is the shortest script and it has no voiceover, so the captions carry the whole ad. The generated demo shows the intended use and the proof is real footage. The surface list names glass and induction only.",
    "running-shoes": "The hook hands the viewer a check they can run right now, the insole test: pull the insole out and stand on it. No injury or podiatrist wording appears anywhere. The generated running shots start from product photos used as reference frames.",
    "travel-esim": "No price is spoken or captioned, and no real airline, airport or carrier branding shows up in the generated shots. The one claim is that there is no contract. The two screen recordings, the install flow and the plan page, are real, so the app shows up exactly as it is.",
    "coffee-subscription": "The hook hands the viewer a check to run on their own bag: find the roast date. The demo shows the bloom in place of taste claims, and the ship date is on the box. The script promises no delivery time.",
    "pet-camera": "The generated camera feed has no timestamp overlay, so it cannot pass for real footage. The real device appears in shot 5. Separation anxiety and vet wording stay out.",
}


# ---------------------------------------------------------------- helpers

def esc(s):
    return html.escape(str(s), quote=True)


def utm(path, medium="pages", content=None):
    q = "utm_source=github&utm_medium=%s&utm_campaign=%s" % (medium, CAMPAIGN)
    if content:
        q += "&utm_content=" + content
    return UNIQ + path + ("&" if "?" in path else "?") + q


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def a_an(n):
    t = str(n)
    return "an" if t.startswith("8") or t in ("11", "18") or (t.startswith("18") and len(t) == 4) else "a"


def cap_first(s):
    return s[:1].upper() + s[1:] if s else s


def plural(n, w):
    return "%d %s%s" % (n, w, "" if n == 1 else "s")


def fit(options, lo, hi, what):
    for o in options:
        if lo <= len(o) <= hi:
            return o
    raise SystemExit("no %s fits %d-%d characters; lengths %s\n%s" % (what, lo, hi, [len(o) for o in options], options[0]))


def fit_desc(options):
    return fit(options, 100, 150, "description")


def fit_title(options):
    for o in options:
        if len(o) <= 60:
            return o
    raise SystemExit("no title fits 60 characters: %s" % options)


def md_inline(s):
    """Backticks to <code>, everything else escaped."""
    parts = s.split("`")
    return "".join(("<code>%s</code>" if i % 2 else "%s") % esc(p) for i, p in enumerate(parts))


def label_hook(t):
    return HOOK_LABEL.get(t, t.replace("-", " "))


def word_count(s):
    return len(s.split())


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def load_examples():
    """({slug: script} in README order, bad script)."""
    names = sorted(f[:-5] for f in os.listdir(os.path.join(ROOT, "examples", "scripts")) if f.endswith(".json"))
    listed = re.findall(r"\[([a-z0-9-]+)\]\(scripts/\1\.json\)", read("examples", "README.md"))
    order = [n for n in listed if n in names]
    order += [n for n in names if n not in order]
    ex = {n: us.load(os.path.join(ROOT, "examples", "scripts", n + ".json")) for n in order}
    bad = us.load(os.path.join(ROOT, "examples", "bad", BAD_SLUG + ".json"))
    return ex, bad


# ---------------------------------------------------------------- compliance cases (verified against lint at build time)

def _set(path, value):
    def f(s):
        cur = s
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = value
    return f


def _shot(i, key, value):
    def f(s):
        s["shots"][i][key] = value
    return f


def _seq(*fns):
    def f(s):
        for fn in fns:
            fn(s)
    return f


def _add_claim(text, evidence):
    def f(s):
        s["claims"].append({"text": text, "evidence": evidence})
    return f


def _hook_late(s):
    s["shots"][0]["end_s"] = 3
    s["shots"][1]["start_s"] = 3


LONG_HOOK = "Your foundation is not the problem, and here is what it really is, trust me"
FAST_VO = "Dry skin drinks the water out of your makeup, so it cakes by lunch, then it slides, then it creases, then it patches, then it flakes again."

CASES = [  # (id, title, what the rule does, where, fail mutation, fail snippet, pass mutation, pass snippet, message regex)
    ("label-missing", "The AI label is empty", "Generated footage needs a label. An empty field means the ad would run without one.",
     'compliance.ai_label', _set(["compliance", "ai_label"], ""), '"ai_label": ""',
     None, '"ai_label": "AI-generated ad"', r"ai_label is empty"),
    ("label-hidden", "The label is not in any caption", "The label only counts when it is on screen in a caption, from the first frame.",
     "compliance.ai_label", _shot(0, "caption", "Your foundation isn't the problem."), 'shot 1 caption: "Your foundation isn\'t the problem."',
     None, 'shot 1 caption: "Your foundation isn\'t the problem.\\nAI-generated ad"', r"ai_label '.*' does not appear in any caption"),
    ("toggle-off", "The platform AI switch is not on", "Set platform_toggle to true once the AI-content setting is switched on in the ad manager.",
     "compliance.platform_toggle", _set(["compliance", "platform_toggle"], False), '"platform_toggle": false',
     None, '"platform_toggle": true', r"platform_toggle"),
    ("presenter-description", "The AI presenter has no description", "A written description helps keep the presenter fictional, so it is not mistaken for a real person.",
     "presenter.description", _set(["presenter", "description"], ""), '"description": ""',
     None, '"description": "fictional woman, late twenties, no makeup, towel on hair; generated, not based on anyone"', r"describe the AI presenter"),
    ("fake-testimonial", "An AI presenter speaks as a customer", "With an AI or no presenter, first-person experience wording is a fabricated testimonial. Lint fails it.",
     "shot 2 voiceover", _shot(1, "voiceover", "I've been using this serum for a month and my skin is glowing."),
     'voiceover: "I\'ve been using this serum for a month and my skin is glowing."',
     None, 'voiceover: "Dry skin drinks the water out of your makeup, so it cakes by lunch."', r"first-person customer testimonial"),
    ("endorsement", "The caption implies an endorsement", "Wording such as \"as seen on\" or \"endorsed by\" suggests that a platform or third party backs the ad.",
     "shot 6 caption", _shot(5, "caption", "as seen on TikTok. link below"), 'caption: "as seen on TikTok. link below"',
     None, 'caption: "link below. try it under your usual base"', r"implies a platform or third-party endorsement"),
    ("claim-unlisted", "A claim is not in the claims list", "Wording like \"best-selling\" is a claim. It has to be listed with the place where it is proven.",
     "shot 6 caption", _shot(5, "caption", "best-selling serum. link below"), 'caption: "best-selling serum. link below"',
     _seq(_shot(5, "caption", "best-selling serum. link below"), _add_claim("best-selling serum", "retailer sales report with a date (example)")),
     'claims: "best-selling serum" with an evidence entry', r"is a claim;"),
    ("number-unbacked", "A number has no claim behind it", "Numbers in a caption or voiceover need an entry in claims. Without one, lint warns.",
     "shot 6 caption", _shot(5, "caption", "over 4000 jars sold. link below"), 'caption: "over 4000 jars sold. link below"',
     _seq(_shot(5, "caption", "over 4000 jars sold. link below"), _add_claim("over 4000 jars sold", "order export with a date (example)")),
     'claims: "over 4000 jars sold" with an evidence entry', r"is not backed by an entry"),
    ("hook-long", "The hook is too long", "A hook over 12 words does not fit in 2 seconds.",
     "hook.text", _seq(_set(["hook", "text"], LONG_HOOK), _shot(0, "voiceover", LONG_HOOK)),
     'hook: "%s"' % LONG_HOOK, None, 'hook: "Your foundation isn\'t the problem."', r"hook is \d+ words"),
    ("hook-late", "The hook shot ends after 2 seconds", "The first shot must end by 2 s.",
     "shot 1 end_s", _hook_late, '"end_s": 3', None, '"end_s": 2', r"hook shot ends at"),
    ("caption-long", "A caption line is too long", "A caption line is capped at 48 characters, so it fits a phone screen.",
     "shot 3 caption", _shot(2, "caption", "two drops of serum, pressed in with two fingers, never rubbed"),
     'caption: "two drops of serum, pressed in with two fingers, never rubbed"',
     None, 'caption: "two drops, press, don\'t rub"', r"caption line is"),
    ("pace-fast", "The voiceover is too fast", "Above 3.5 words per second a shot is hard to follow. Lint warns.",
     "shot 2 voiceover", _shot(1, "voiceover", FAST_VO), "voiceover: %d words in a 5 s shot" % word_count(FAST_VO),
     None, "voiceover: 14 words in a 5 s shot", r"voiceover runs at"),
]


def build_cases(base):
    """Run every case through lint. Returns [{...}] with the real message for each failing case."""
    r0 = us.lint(base)
    if r0.errors or r0.warnings:
        raise SystemExit("compliance base script is not clean: %s %s" % (r0.errors, r0.warnings))
    out = []
    for cid, title, why, where, fmut, fsnip, pmut, psnip, rx in CASES:
        bad = copy.deepcopy(base)
        fmut(bad)
        r = us.lint(bad)
        hits = [("Error", m) for _, m in r.errors if re.search(rx, m)] + [("Warning", m) for _, m in r.warnings if re.search(rx, m)]
        if not hits:
            raise SystemExit("case %s: lint did not raise %s; got %s %s" % (cid, rx, r.errors, r.warnings))
        good = copy.deepcopy(base)
        if pmut:
            pmut(good)
        rg = us.lint(good)
        if rg.errors or rg.warnings:
            raise SystemExit("case %s: the passing version is not clean: %s %s" % (cid, rg.errors, rg.warnings))
        out.append({"id": cid, "title": title, "why": why, "where": where, "fail": fsnip, "pass": psnip,
                    "level": hits[0][0], "message": friendly((re.match(r"shot \d+", where) or [where])[0] if re.match(r"shot \d+", where) else where, hits[0][1], bad), "all": [m for _, m in r.errors + r.warnings]})
    return out


def rule_for(msg):
    for rx, why in RULES:
        if re.search(rx, msg):
            return why
    return ""


# Plain-language wording for each lint message, shown on the site. The CLI keeps its own wording (the READMEs quote it).
# Tokens: {N} is regex group N, {N-1} is group N minus one, {q} is the offending words quoted from the shot.
# checker.js applies the same table, so the browser and the built pages read the same.
PRES_FRIENDLY = {"ai_generated": "an AI presenter", "none": "a script with no presenter", "real_person": "a real person"}
FRIENDLY = [
    (r"^hook is (\d+) words; keep it under (\d+) so it lands inside ([\d.]+) s$",
     "The hook is {1} words. Keep it to {2-1} words or fewer so it lands inside {3} seconds."),
    (r"^caption line is (\d+) chars; keep lines under (\d+) for a phone screen$",
     "This caption line is {1} characters. Keep each line to {2-1} characters max so it fits a phone screen."),
    (r"^voiceover runs at ([\d.]+) words/s over ([\d.]+s); above --max-wps ([\d.]+)$",
     "The voiceover speaks {1} words per second over {2}, above the {3} words per second pace. Cut words or give the shot more time."),
    (r"^reads as a first-person customer testimonial but the presenter is '(.*)': rewrite as a demo or a sourced claim$",
     "{q} reads as a customer testimonial, and {1} cannot give one. Rewrite it as a demo or a sourced claim."),
    (r"^implies a platform or third-party endorsement; remove it$",
     "{q} suggests a platform or another party endorses the ad. Remove it."),
    (r"^'(.*)' is a claim; list it under 'claims' with evidence or drop it$",
     "\"{1}\" is a claim. Add it to the claims list with evidence, or take it out."),
    (r"^number '(.*)' is not backed by an entry in 'claims'$",
     "The number \"{1}\" has no source. Add it to the claims list with evidence, or take it out."),
    (r"^'(.*)' has no evidence; add a source, a test or remove the claim$",
     "\"{1}\" is in the claims list without evidence. Add a source or a test, or remove the claim."),
    (r"^ai_label is empty but ", "Add an on-screen AI label such as \"AI-generated ad\": the ad has generated footage or an AI presenter."),
    (r"^ai_label '(.*)' does not appear in any caption; put it on screen$",
     "The label \"{1}\" is not in any caption. Add it as a caption line from the first shot."),
    (r"^set platform_toggle: true once", "Switch on the platform's AI-content setting when you upload, then tick the box for it here."),
    (r"^describe the AI presenter so", "Describe the AI presenter in a sentence, as a fictional person who is not modelled on anyone real."),
    (r"^prompt is missing 'subject'$", "This shot's prompt has no subject. Add one, e.g. a bare foot on a grey insole."),
    (r"^prompt is missing 'action'$", "This shot's prompt has no action. Add one, e.g. the foot settles onto the insole."),
    (r"^prompt is missing 'camera'$", "This shot's prompt has no camera move. Add one, e.g. slow push-in."),
    (r"^prompt is missing '(.*)'$", "This shot's prompt has no {1}. Fill it in."),
    (r"^AI-generated shot has no 'prompt' object$", "A generated shot needs a prompt with a subject, an action and a camera move, e.g. slow push-in."),
    (r"^missing field\(s\): (.*)$", "This shot is missing {1}. Fill each one in, e.g. a visual that says what is on screen, or a beat of demo."),
    (r"^missing top-level field '(.*)'$", "The script has no \"{1}\" field. Add it, or start from the blank script."),
    (r"^type must be one of (.*)$", "Set the presenter type to one of: {1}."),
    (r"^source must be one of (.*)$", "Set the shot source to one of: {1}."),
    (r"^hook text is empty$", "Write the hook text: 12 words or fewer, said and captioned together."),
    (r"^hook text does not appear in the first shot", "Say or caption the hook text in the first shot, so the viewer reads what they hear."),
    (r"^first shot must be the hook$", "The first shot must be the hook. Set its beat to hook."),
    (r"^first shot must start at 0$", "The first shot must start at 0 seconds."),
    (r"^hook shot ends at ([\d.]+s); it must end by ([\d.]+s)$", "The hook shot ends at {1}. End it by {2} so the hook lands in time."),
    (r"^missing beat\(s\): (.*)$", "The script has no {1} beat. Every script runs hook, problem, demo, proof, cta."),
    (r"^beats out of order: (.*); expected (.*)$", "The beats run {1}. Put them in this order: {2}."),
    (r"^last shot must be the cta$", "The last shot must be the cta beat."),
    (r"^starts at ([\d.]+s) but previous shot ended at ([\d.]+s)$", "This shot starts at {1} but the previous one ended at {2}. Make the times meet."),
    (r"^end_s must be greater than start_s$", "This shot must end after it starts. Raise its end time."),
    (r"^shots end at ([\d.]+s) but duration_s is ([\d.]+s)$", "The shots end at {1}, but the script length is {2}. Make the two match."),
    (r"^over 60 s", "This script runs over 60 seconds. Check each platform's current ad length limit."),
    (r"^no shots$", "Add at least one shot."),
    (r"^cta text is empty$", "Write the spoken call to action."),
]
REF_URL = {"compliance": "compliance/", "structure": "", "script-format": "", "model-prompts": ""}
COMPLIANCE_ANCHORS = {"1": "disclose", "2": "testimonials", "3": "impersonation", "4": "claims"}


def quote_for(where, script, kind):
    """The words in the shot that tripped a testimonial (kind t) or endorsement (kind e) rule, in quotes."""
    m = re.match(r"^shot (\d+)$", where or "")
    try:
        shot = script["shots"][int(m.group(1)) - 1]
    except (TypeError, KeyError, IndexError, AttributeError, ValueError):
        return ""
    if not isinstance(shot, dict):
        return ""
    if kind == "t":
        text, pats = "%s %s" % (shot.get("voiceover", ""), shot.get("caption", "")), us.TESTIMONIAL_PATTERNS
    else:
        text, pats = us.shot_text(shot), us.IMPERSONATION_PATTERNS
    for pat in pats:
        hit = re.search(pat, text, re.I)
        if hit:
            return "\u201c%s\u201d" % hit.group(0)
    return ""


def friendly(where, msg, script):
    for rx, to in FRIENDLY:
        m = re.search(rx, msg)
        if not m:
            continue

        def sub(t):
            k = t.group(1)
            if k == "q":
                return quote_for(where, script, "t" if "testimonial" in rx else "e") or "This wording"
            v = m.group(int(k)) if int(k) <= len(m.groups()) else ""
            v = v or ""
            if t.group(2):
                v = str(int(v) - 1)
            return PRES_FRIENDLY.get(v, v)
        return re.sub(r"\{(q|\d)(-1)?\}", sub, to)
    return msg


def why_html(why, prefix=""):
    """The Why text with its (compliance.md, section 2) style references turned into links."""
    def link(m):
        ref, sec_no = m.group(1), m.group(2)
        if ref == "compliance":
            href = prefix + "compliance/" + ("#" + COMPLIANCE_ANCHORS[sec_no] if sec_no in COMPLIANCE_ANCHORS else "")
            return '(<a href="%s">compliance rules%s</a>)' % (href, ", section " + sec_no if sec_no else "")
        return '(<a href="%s/blob/main/references/%s.md" rel="noopener">%s.md</a>)' % (REPO, ref, ref)
    return re.sub(r"\((compliance|structure|script-format|model-prompts)\.md(?:, section (\d))?\)", link, esc(why))


# ---------------------------------------------------------------- hook library parsed from references/

def parse_hooks():
    txt = read("references", "hook-library.md")
    cats, avoid, cur = [], [], None
    for line in txt.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            if name == "Hooks to avoid":
                cur = "avoid"
            else:
                cur = []
                cats.append((name, cur))
        elif line.startswith("- "):
            if cur == "avoid":
                avoid.append(line[2:].strip())
            elif cur is not None:
                m = re.match(r"- ([A-Z]+) · (.+)$", line)
                if not m:
                    raise SystemExit("bad hook line: " + line)
                tag, text = m.group(1), m.group(2).strip()
                if tag not in TAG_NAMES:
                    raise SystemExit("unknown hook tag " + tag)
                if word_count(text) > us.HOOK_MAX_WORDS:
                    raise SystemExit("hook over %d words: %s" % (us.HOOK_MAX_WORDS, text))
                cur.append((tag, text))
    if not cats or not avoid:
        raise SystemExit("hook-library.md: nothing parsed")
    return cats, avoid


HOOK_ANSWER = ("A UGC hook works in 2 seconds when it carries one idea in 12 words or fewer, said and captioned at once, and the first shot ends by 2 s. "
               "Lint fails a longer hook or a later cut. Name the viewer's own situation or show the payoff, and skip generic openers such as \"Stop scrolling\".")
COMPLIANCE_ANSWER = ("An AI-generated UGC ad needs a plain on-screen label from the first frame, the platform's AI-content setting switched on, a fictional presenter who only demonstrates, "
                     "and a dated source for every measurable claim. Put the label in a caption, not in a corner the app can cover. This is a working checklist, not legal advice.")
assert 40 <= word_count(HOOK_ANSWER) <= 60, word_count(HOOK_ANSWER)
assert 40 <= word_count(COMPLIANCE_ANSWER) <= 60, word_count(COMPLIANCE_ANSWER)
HOME_ANSWER = ("A UGC ad script checker reads a script and runs lint rules on it: a hook of 12 words or fewer that ends by 2 seconds, five beats in order, "
               "an on-screen AI label, claims backed by evidence and no fake testimonials. It then writes a first-frame image, Kling and Veo 3 prompt for every generated shot.")
EXAMPLES_ANSWER = ("A finished UGC ad script has a hook, five beats from problem to call to action, captions, a voiceover and a source for every shot, with each claim listed beside its evidence. "
                   "Every example here passes lint, and every generated shot has a prompt for Kling and Veo 3.")
assert 40 <= word_count(HOME_ANSWER) <= 60, word_count(HOME_ANSWER)
assert 40 <= word_count(EXAMPLES_ANSWER) <= 60, word_count(EXAMPLES_ANSWER)


def faq_items(n_hooks):
    return [
        ("How long should a UGC ad hook be?",
         "Keep it to 12 words or fewer and make the first shot end by 2 seconds. The hook is said and captioned at once, because the sound is often off. "
         "Lint fails a longer hook, and the hook library has %d starting points across 10 product categories." % n_hooks),
        ("Why does an AI-generated ad need an on-screen label?",
         "Generated footage needs a plain label, such as AI-generated ad, in a caption from the first frame. The label must be in a caption because the app's own buttons can cover a corner. "
         "Lint fails an empty label, and it warns when the platform's AI-content setting is not marked as switched on."),
        ("What counts as a fake testimonial?",
         "An AI presenter that says it used the product, such as \"I've been using this\" or \"changed my life\". Star ratings and \"verified buyer\" wording are claims about real people too. "
         "An AI presenter is a demonstrator: it can show, explain and compare. A real customer can testify with a signed release, proof of purchase and a paid-partnership disclosure."),
        ("Kling or Veo 3 for a shot with dialogue?",
         "Veo 3 prompts include a Dialogue line when the shot has a voiceover, the presenter is not none and on_camera_speech is not false. "
         "Kling prompts describe the visuals only, so record or synthesise the voiceover separately and lay it over the clip."),
        ("Is the checker free, and does it send my script anywhere?",
         "It is free. The code and examples are MIT and the vocabulary in references/ is CC BY 4.0. Page views and button clicks are counted anonymously; your script text never leaves the browser. "
         "Generating the video elsewhere may cost credits, depending on the tool."),
        ("Does a clean lint mean the ad is compliant?",
         "No. Lint checks the rules in this repo and nothing more. Platform policies and laws change, and the notes were last checked on %s. "
         "Treat compliance.md as a working checklist, not legal advice, and re-read each platform's current ad policy before you run an ad." % RULES_CHECKED),
    ]


# ---------------------------------------------------------------- server-side markup (mirrors the functions in checker.js)

def lint_html(r, script=None, prefix=""):
    ok = not r.errors
    h = '<p class="ug-sum %s"><b>%s</b> <span>%s · %s</span></p>' % (
        "ug-sum-ok" if ok else "ug-sum-bad", "✓ Lint passed" if ok else "✕ Lint failed",
        plural(len(r.errors), "error"), plural(len(r.warnings), "warning"))
    items = ""
    for cls, tag, label, pairs in (("ug-err", "✕", "Error", r.errors), ("ug-warn", "!", "Warning", r.warnings)):
        for where, msg in pairs:
            why = rule_for(msg)
            items += '<li class="%s"><b aria-label="%s">%s %s</b> <span class="ug-where">%s</span> %s%s</li>' % (
                cls, label, tag, label, esc(where), esc(friendly(where, msg, script)),
                (' <small class="ug-why">Why: %s</small>' % why_html(why, prefix)) if why else "")
    if items:
        h += '<ul class="ug-checks">%s</ul>' % items
    return h


def stats_html(st):
    rows = [
        ("Hook ends at", us.fmt_s(st["hook_ends_s"]) if st["hook_ends_s"] is not None else "not set"),
        ("Hook words", "%d (limit %d)" % (st["hook_words"], us.HOOK_MAX_WORDS)),
        ("Shots", str(st["shots"])),
        ("Voiceover words", str(st["voiceover_words"])),
        ("Words per second", "%r (ceiling %s)" % (float(st["voiceover_wps"]), us.DEFAULT_MAX_WPS)),
        ("Caption lines", "%d, longest %d chars (limit %d)" % (st["caption_lines"], st["longest_caption_line"], us.CAPTION_MAX_CHARS)),
        ("AI-generated shots", str(st["ai_generated_shots"])),
        ("Claims listed", str(st["claims"])),
    ]
    return '<div class="ug-tablewrap"><table class="ug-table ug-kv"><tbody>%s</tbody></table></div>' % "".join(
        '<tr><th scope="row">%s</th><td>%s</td></tr>' % (esc(k), esc(v)) for k, v in rows)


def tabs_html(model):
    return "".join(
        '<button type="button" role="tab" class="ug-tab" data-model="%s" aria-selected="%s" tabindex="%d">%s</button>' % (
            k, "true" if k == model else "false", 0 if k == model else -1, MODEL_LABEL[k]) for k in ("image", "kling", "veo3"))


def is_generated(s):
    return s.get("source") in ("image-to-video", "text-to-video") or bool(s.get("prompt"))


def shot_prompts(script, model):
    out = []
    for i, s in enumerate(script["shots"]):
        if not is_generated(s):
            continue
        if model == "image" and s.get("source") != "image-to-video":
            continue
        out.append({"i": i, "id": s.get("id", i + 1), "beat": s.get("beat", ""), "start": s["start_s"], "end": s["end_s"],
                    "text": us.MODELS[model](s, script)})
    return out


CTA = {
    "image": {"text": "Use each first frame as the start image in an image-to-video generator.",
              "url": utm("/video/image-to-video", content="checker-image"), "label": "Try an image-to-video generator"},
    "kling": {"text": "UniqAI runs Kling and Veo among others.", "url": utm("/models/kling", content="checker-kling"),
              "label": "Run Kling prompts on UniqAI"},
    "veo3": {"text": "UniqAI runs Kling and Veo among others.", "url": utm("/models/veo", content="checker-veo3"),
             "label": "Run Veo 3 prompts on UniqAI"},
}
CTA_PATH = {"image": "/video/image-to-video", "kling": "/models/kling", "veo3": "/models/veo"}


def cta_html(model, content=None):
    c = CTA[model]
    url, out = c["url"], "checker-" + model
    if content:
        url, out = utm(CTA_PATH[model], content=content + "-" + model), "example-" + model
    return '<div class="ug-cta"><p>%s</p><a class="btn btn-ghost" href="%s" data-out="%s">%s</a></div>' % (
        esc(c["text"]), esc(url), out, esc(c["label"]))


def panel_html(script, model, slug, example=False):
    lst = shot_prompts(script, model)
    h = ""
    if not lst:
        h += '<p class="ug-small">%s</p>' % (
            "No image-to-video shots in this script, so there is no first-frame prompt to write." if model == "image"
            else "No AI-generated shots in this script, so there is nothing to prompt for.")
    allp = []
    for it in lst:
        pid = "ug-p-%s-%d" % (model, it["i"])
        span = "%s-%s" % (us.fmt_s(it["start"]), us.fmt_s(it["end"]))
        allp.append("## shot %s · %s · %s\n%s" % (it["id"], it["beat"], span, it["text"]))
        h += ('<div class="ug-shotp"><div class="ug-row"><h4>Shot %s · %s · %s</h4><span class="ug-count">%d characters</span></div>'
              '<pre id="%s" tabindex="0">%s</pre><p class="ug-actions"><button type="button" class="btn btn-sm btn-copy" data-copy="%s" '
              'data-kind="prompt" data-model="%s" data-example="%s" data-label="Copy prompt">Copy prompt</button></p></div>' % (
                  esc(it["id"]), esc(it["beat"]), span, len(it["text"]), pid, esc(it["text"]), pid, model, esc(slug or "custom")))
    if len(lst) > 1:
        h += ('<pre id="ug-all-%s" hidden>%s</pre><p class="ug-actions"><button type="button" class="btn btn-sm btn-ghost btn-copy" '
              'data-copy="ug-all-%s" data-kind="prompt" data-model="%s" data-example="%s" data-label="Copy all %d prompts">Copy all %d prompts</button></p>' % (
                  model, esc("\n\n".join(allp)), model, model, esc(slug or "custom"), len(lst), len(lst)))
    return h + cta_html(model, slug if example else None)


# ---------------------------------------------------------------- page chrome

def head(title, desc, path, graph, prefix, noindex=False):
    url = BASE + path
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title>
<meta name="description" content="{d}">
<link rel="canonical" href="{u}">
<meta name="robots" content="{rb}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="UniqAI open source">
<meta property="og:locale" content="en_US">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:url" content="{u}">
<meta property="og:image" content="{img}">
<meta property="og:image:width" content="{iw}">
<meta property="og:image:height" content="{ih}">
<meta property="og:image:alt" content="ugc-ad-director: lint a UGC ad script and copy per-shot prompts for Kling and Veo 3">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{t}">
<meta name="twitter:description" content="{d}">
<meta name="twitter:image" content="{img}">
<meta name="theme-color" content="#0b1113">
<link rel="icon" href="{p}assets/favicon.svg" type="image/svg+xml">
<link rel="preload" href="{p}assets/fonts/Geist-Variable.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{p}site.css">
<link rel="alternate" type="text/plain" href="{b}llms.txt" title="llms.txt">
<script type="application/ld+json">{j}</script>
</head>
""".format(t=esc(title), d=esc(desc), u=url, b=BASE, p=prefix, img=OG_IMAGE, iw=OG_SIZE[0], ih=OG_SIZE[1],
           rb="noindex" if noindex else "index, follow, max-image-preview:large",
           j=jdump({"@context": "https://schema.org", "@graph": graph}))


def header(prefix, current):
    links = [("", "Checker"), ("examples/", "Examples"), ("hooks/", "Hooks"), ("compliance/", "Compliance")]
    nav = "".join('<a href="{p}{h}"{c}>{l}</a>'.format(p=prefix, h=h, l=l, c=' aria-current="page"' if h == current else "")
                  for h, l in links)
    return """<header class="top"><div class="wrap top-in">
<a class="brand" href="{hub}" aria-label="UniqAI open-source tools"><img src="{p}assets/wordmark.svg" alt="UniqAI" width="96" height="28"><span>open source</span></a>
<nav class="nav" aria-label="Sections">{nav}<a href="{repo}" rel="noopener">GitHub</a></nav>
<a class="btn btn-sm" href="{cta}" data-out="header">Make a video</a>
</div></header>
""".format(p=prefix, nav=nav, repo=REPO, hub=HUB, cta=esc(utm("/video/text-to-video", content="header")))


def browse_links(examples):
    out = [("", "Script checker"), ("examples/", "All examples"), ("hooks/", "Hook library"), ("compliance/", "Compliance rules")]
    out += [("examples/%s/" % s, b["title"]) for s, b in examples.items()]
    return out


def footer(prefix, examples, builder=False):
    browse = "".join('<a href="{p}{h}">{l}</a>'.format(p=prefix, h=h, l=esc(l)) for h, l in browse_links(examples))
    tools = "".join(
        '<li><a href="https://github.com/uniqaiapp/{n}" rel="noopener"><strong>{t}</strong><span>{d}</span></a></li>'.format(
            n=n, t=esc(t), d=esc(d)) for n, t, d in TOOLS)
    scripts = '<script src="{p}site.js" defer></script>'.format(p=prefix)
    if builder:
        scripts += '\n<script src="{p}checker.js" defer></script>'.format(p=prefix)
    return """<nav class="browse wrap" aria-label="Browse the checker, examples, hooks and rules"><h2>Browse the checker, hooks, rules and examples</h2><p>{browse}</p></nav>
<section class="more wrap" aria-labelledby="more-h">
<h2 id="more-h">More free tools from UniqAI</h2>
<ul class="tools">{tools}
<li><a href="https://github.com/hablamusic" rel="noopener"><strong>HablaMusic</strong><span>Sister project: free AI music and DJ tools (Suno prompts, BPM and key finder).</span></a></li>
</ul></section>
<footer class="foot"><div class="wrap foot-in">
<p><strong>UniqAI</strong> makes AI video, image and chat tools at <a href="{home}" data-out="footer">uniqai.app</a>. This tool is open source: code and examples under MIT, the vocabulary in references/ under CC BY 4.0, on <a href="{repo}" rel="noopener">GitHub</a>.</p>
<p>Model names are trademarks of their owners. Not affiliated with Kuaishou or Google.</p>
<p>Curated by <a href="https://www.roimobi.com/?utm_source=github&amp;utm_medium=pages&amp;utm_campaign={c}" rel="noopener">Vlad Yasinski</a> · <a href="{li}" rel="noopener">LinkedIn</a> · <a href="https://t.me/ua5er" rel="noopener">Telegram</a></p>
</div></footer>
<script>window.UQ={{key:"{ph}",repo:"{c}"}};</script>
{scripts}
</body></html>
""".format(browse=browse, tools=tools, home=esc(utm("/", content="footer")), repo=REPO, c=CAMPAIGN, li=LINKEDIN,
           ph=POSTHOG_KEY, scripts=scripts)


def breadcrumb(items):
    return {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": BASE + p} for i, (n, p) in enumerate(items)]}


def org():
    return {"@type": "Organization", "@id": UNIQ + "/#org", "name": "UniqAI", "url": UNIQ + "/",
            "logo": UNIQ + "/icon-512.png", "sameAs": ["https://github.com/uniqaiapp"]}


def website():
    return {"@type": "WebSite", "@id": HUB + "#site", "name": "UniqAI open-source tools", "url": HUB}


def webapp():
    return {"@type": "WebApplication", "@id": BASE + "#app", "name": "ugc-ad-director script checker", "url": BASE,
            "applicationCategory": "MultimediaApplication", "operatingSystem": "Any (web browser)",
            "description": "Lint a UGC ad script for the hook, the AI label, claims and fake testimonials, then copy per-shot prompts for image models, Kling and Veo 3.",
            "isAccessibleForFree": True, "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "license": REPO + "/blob/main/LICENSE", "author": AUTHOR, "publisher": {"@id": UNIQ + "/#org"},
            "codeRepository": REPO, "image": OG_IMAGE, "sameAs": [REPO]}


def page(path, title, desc, h1, lead, body, examples, crumbs, current="", kind="CollectionPage", extra_graph=None,
         buttons=None, skip="#prompts", skip_label="Skip to the content", quote="", builder=False, main_entity=None):
    prefix = "../" * path.count("/")
    graph = [org(), website(),
             {"@type": kind, "@id": BASE + path + "#page", "url": BASE + path, "name": title, "description": desc,
              "inLanguage": "en", "isPartOf": {"@id": HUB + "#site"}, "publisher": {"@id": UNIQ + "/#org"},
              "license": "https://creativecommons.org/licenses/by/4.0/", "dateModified": SITE_UPDATED,
              "author": AUTHOR, "primaryImageOfPage": {"@type": "ImageObject", "url": OG_IMAGE}}]
    if main_entity:
        graph[2]["mainEntity"] = {"@id": main_entity}
    if len(crumbs) > 1:
        graph.append(breadcrumb(crumbs))
    graph += extra_graph or []
    crumb_html = ""
    if len(crumbs) > 1:
        crumb_html = '<nav class="crumbs" aria-label="Breadcrumb">%s</nav>' % " / ".join(
            '<a href="%s%s">%s</a>' % (prefix, p, esc(n)) if i < len(crumbs) - 1 else '<span>%s</span>' % esc(n)
            for i, (n, p) in enumerate(crumbs))
    btns = "".join(buttons or [])
    out = """<body>
<a class="skip" href="{skip}">{skipl}</a>
{header}
<main>
<section class="hero"><div class="wrap hero-in">
{crumbs}
<p class="pill"><span class="dot"></span>free · open source · MIT + CC BY 4.0</p>
<h1>{h1}</h1>
{quote}<p class="lead">{lead}</p>
<p class="byline">Page updated {upd} · Rules checked {chk} · Curated by Vlad Yasinski (<a href="{li}" rel="noopener">LinkedIn</a>, <a href="{rm}" rel="noopener">roimobi.com</a>) · Open source on <a href="{repo}" rel="noopener">GitHub</a></p>
<div class="hero-cta">{btns}</div>
</div></section>
{body}
</main>
""".format(skip=skip, skipl=skip_label, header=header(prefix, current), crumbs=crumb_html, h1=esc(h1),
           quote='<p class="ug-quote">%s</p>\n' % esc(quote) if quote else "", lead=esc(lead), upd=SITE_UPDATED, chk=RULES_CHECKED,
           rm=esc("https://www.roimobi.com/?utm_source=github&utm_medium=pages&utm_campaign=" + CAMPAIGN),
           li=LINKEDIN, repo=REPO, btns=btns, body=body)
    return head(title, desc, path, graph, prefix) + out + footer(prefix, examples, builder)


def btn(href, label, ghost=False, out=None):
    return '<a class="btn%s" href="%s"%s>%s</a>' % (" btn-ghost" if ghost else "", esc(href),
                                                      ' data-out="%s"' % out if out else "", esc(label))


def table(headers, rows, cls="ug-table"):
    th = "".join('<th scope="col">%s</th>' % esc(h) for h in headers)
    trs = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % esc(c) for c in row) for row in rows)
    return '<div class="ug-tablewrap"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (cls, th, trs)


def kv_table(rows):
    return '<div class="ug-tablewrap"><table class="ug-table ug-kv"><tbody>%s</tbody></table></div>' % "".join(
        '<tr><th scope="row">%s</th><td>%s</td></tr>' % (esc(k), esc(v)) for k, v in rows)


def sec(title, inner, sid="", cls="ug-sec wrap"):
    return '<section class="%s"%s>\n<h2>%s</h2>\n%s\n</section>' % (cls, ' id="%s"' % sid if sid else "", esc(title), inner)


def answer_sec(question, answer, sid="answer"):
    return sec(question, '<p class="ug-answer">%s</p>' % esc(answer), sid)


def faq_html(items):
    return '<section class="faq wrap" aria-labelledby="faq-h"><h2 id="faq-h">Questions</h2>%s</section>' % "".join(
        "<details><summary>%s</summary><p>%s</p></details>" % (esc(q), esc(a)) for q, a in items)


def faq_graph(items):
    return {"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q,
                                                 "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in items]}


def example_list(examples, exclude=None):
    items = []
    for slug, b in examples.items():
        if slug == exclude:
            continue
        st = us.stats(b)
        items.append('<li><a href="{p}examples/{s}/"><strong>{t}</strong>{i}<em>{d} s · {pr} · {n} shots</em></a></li>'.format(
            p="{P}", s=slug, t=esc(b["title"]), i=esc("Hook: " + b["hook"]["text"]), d=us.fmt_s(b["duration_s"])[:-1],
            pr=PRESENTER_LABEL[b["presenter"]["type"]], n=st["shots"]))
    return '<ul class="ug-list">%s</ul>' % "".join(items)


# ---------------------------------------------------------------- example pages

def source_mix(b):
    counts = {}
    for s in b["shots"]:
        counts[s["source"]] = counts.get(s["source"], 0) + 1
    return ", ".join("%d %s" % (n, k) for k, n in counts.items())


CATEGORY = {"skincare-serum": "Skincare", "meal-prep-app": "Meal Prep App", "kitchen-chopper": "Kitchen Gadget", "dog-dental-chews": "Pet Care",
            "language-course": "Online Course", "b2b-invoicing": "B2B SaaS", "cleaning-spray": "Home Cleaning", "running-shoes": "Running Shoes",
            "travel-esim": "Travel eSIM", "coffee-subscription": "Coffee Subscription", "pet-camera": "Pet Camera"}


def brand_of(b):
    return b["product"]["name"]


def example_title(slug, b):
    cat, name = CATEGORY[slug], brand_of(b)
    return fit_title(["UGC Ad Script for %s: %s | UniqAI" % (cat, name),
                      "UGC Ad Script for %s: %s | UniqAI" % (cat, name.split()[0]),
                      "UGC Ad Script for %s | UniqAI" % cat])


def example_desc(slug, b, st):
    cat, name, d, n = CATEGORY[slug], brand_of(b), int(b["duration_s"]), st["shots"]
    first = name.split()[0]
    opts = []
    for nm in (name, first):
        opts += ["UGC ad script for %s: %s. %d-second %s ad, %d shots, captions and Kling and Veo 3 prompts. Passes lint. Free and open source." % (cat.lower(), nm, d, b["vertical"], n),
                 "UGC ad script for %s: %s. %d-second ad, %d shots, captions and Kling and Veo 3 prompts. Free and open source." % (cat.lower(), nm, d, n),
                 "UGC ad script for %s: %s. %d s, %d shots, captions and Kling and Veo 3 prompts. Free, open source." % (cat.lower(), nm, d, n)]
    return fit_desc(opts)


def about_sentence(b, st):
    gen, real = st["ai_generated_shots"], st["shots"] - st["ai_generated_shots"]
    parts = []
    if gen:
        parts.append("%d generated" % gen)
    if real:
        parts.append("%d filmed or screen-recorded" % real)
    vo = "Voiceover pace %s words/s." % st["voiceover_wps"] if st["voiceover_words"] else "No voiceover; the captions carry the ad."
    return "%d shots: %s. %s" % (st["shots"], ", ".join(parts), vo)


def how_answer(slug, b, st):
    gen, real = st["ai_generated_shots"], st["shots"] - st["ai_generated_shots"]
    hook = b["hook"]
    mix = ("%d are generated and %d are real footage, screen recording or still" % (gen, real)) if gen and real else (
        "all %d are generated" % gen if gen else "none is generated")
    opts = ['%s runs %d seconds and opens with a %s hook, \u201c%s\u201d %s Of its %d shots, %s. It passes lint, and each generated shot has Kling and Veo 3 prompts.' % (
                brand_of(b), int(b["duration_s"]), label_hook(hook.get("type", "")), hook["text"], hook.get("why", ""), st["shots"], mix),
            '%s runs %d seconds and opens with a %s hook, \u201c%s\u201d Of its %d shots, %s. It passes lint, and each generated shot has Kling and Veo 3 prompts.' % (
                brand_of(b), int(b["duration_s"]), label_hook(hook.get("type", "")), hook["text"], st["shots"], mix),
            '%s runs %d seconds and opens with a %s hook, \u201c%s\u201d Of its %d shots, %s. It passes lint, with prompts for each generated shot.' % (
                brand_of(b), int(b["duration_s"]), label_hook(hook.get("type", "")), hook["text"], st["shots"], mix)]
    for o in opts:
        if 40 <= word_count(o) <= 60:
            return o
    raise SystemExit("no 40-60 word answer for %s: %s" % (slug, [word_count(o) for o in opts]))


def timeline_table(b):
    rows = []
    for s in b["shots"]:
        rows.append((s["id"], "%s–%s" % (us.fmt_s(s["start_s"]), us.fmt_s(s["end_s"])), s["beat"], s["visual"],
                     str(s.get("caption", "")).replace("\n", " / "), s.get("voiceover", ""), s["source"]))
    return table(["#", "Time", "Beat", "Visual", "Caption", "Voiceover", "Source"], rows)


def example_body(slug, b, examples):
    st = us.stats(b)
    pres, comp, hook = b["presenter"], b["compliance"], b["hook"]
    about = [("Product", "%s: %s" % (b["product"]["name"], b["product"].get("one_liner", ""))), ("Vertical", b["vertical"]),
             ("Platforms", ", ".join(PLATFORM_LABEL.get(p, p) for p in b.get("platforms", []))),
             ("Length", "%s" % us.fmt_s(b["duration_s"])), ("Aspect", b.get("aspect", "9:16")), ("Audience", b.get("audience", "")),
             ("Presenter", PRESENTER_LABEL[pres["type"]] + (": " + pres["description"] if pres.get("description") else "")),
             ("Hook type", label_hook(hook.get("type", ""))), ("Shot sources", source_mix(b)),
             ("Claims listed", str(st["claims"]))]
    body = sec("How does this %s UGC ad script work?" % CATEGORY[slug].lower(), '<p class="ug-answer">%s</p>' % esc(how_answer(slug, b, st)), "answer")
    body += "\n" + sec("About this script", kv_table(about) + '<p class="ug-small">%s</p>' % esc(about_sentence(b, st)), "about")
    body += "\n" + sec("Hook (ends at %s)" % us.fmt_s(b["shots"][0]["end_s"]),
                       '<blockquote class="ug-quote">%s</blockquote><p class="ug-small">Type: %s. %s</p>' % (
                           esc(hook["text"]), esc(label_hook(hook.get("type", ""))), esc(hook.get("why", ""))), "hook")
    body += "\n" + sec("Timeline", timeline_table(b), "timeline")
    vo = "".join("<li><strong>%s</strong> %s</li>" % (us.fmt_s(s["start_s"]), esc(s["voiceover"])) for s in b["shots"] if s.get("voiceover"))
    vo_html = ('<ul class="ug-notes">%s</ul><p class="ug-small">%d words over %s (%s words/s).</p>' % (
        vo, st["voiceover_words"], us.fmt_s(b["duration_s"]), st["voiceover_wps"])) if vo else \
        '<p class="ug-small">This script has no voiceover. The captions carry the ad.</p>'
    caps = "".join("<li><code>%s</code> %s</li>" % (us.fmt_s(s["start_s"]), esc(line)) for s in b["shots"]
                   for line in str(s.get("caption", "")).split("\n") if line)
    body += "\n" + sec("Voiceover and captions", "<h3>Voiceover script</h3>%s<h3>On-screen captions</h3><ul class=\"ug-notes\">%s</ul>" % (vo_html, caps), "words")
    cards = ""
    for k in ("image", "kling", "veo3"):
        cards += '<article class="ug-model" id="model-%s"><h3>%s</h3>\n%s\n</article>\n' % (k, MODEL_FULL[k], panel_html(b, k, slug, example=True))
    real = [s for s in b["shots"] if not is_generated(s)]
    if real:
        cards += '<h3>Shots that are not generated</h3><ul class="ug-notes">%s</ul>' % "".join(
            "<li>Shot %s (%s): %s</li>" % (s["id"], esc(s["source"]), esc(s["visual"])) for s in real)
    body += "\n" + sec("Shot prompts", cards, "prompts")
    body += "\n" + sec("Call to action", "<p>Spoken: %s</p><p>On screen: %s</p>" % (esc(b["cta"]["text"]), esc(b["cta"].get("on_screen", ""))), "cta")
    claims = b.get("claims", [])
    body += "\n" + sec("Claims and evidence", table(["Claim", "Evidence"], [(c["text"], c["evidence"]) for c in claims]) if claims
                       else "<p>No factual claims in this script.</p>", "claims")
    pres_line = {"ai_generated": "fictional, not modelled on any real person", "real_person": "real person with a signed release",
                 "none": "no presenter"}[pres["type"]]
    comp_items = ['<li>AI label on screen: <code>%s</code></li>' % esc(comp.get("ai_label", "")),
                  "<li>Platform AI-content toggle: %s</li>" % ("on" if comp.get("platform_toggle") else "not yet set"),
                  "<li>Presenter: %s; %s</li>" % (esc(pres["type"].replace("_", " ")), pres_line)]
    comp_items += ["<li>%s</li>" % esc(n) for n in comp.get("notes", [])]
    body += "\n" + sec("Compliance notes", '<ul class="ug-notes">%s</ul><p class="ug-small">See the <a href="../../compliance/">compliance rules</a>. '
                       'A working checklist, not legal advice.</p>' % "".join(comp_items), "compliance")
    body += "\n" + sec("Lint result", lint_html(us.lint(b), b, "../../") + '<p class="ug-small">Run by <code>ugc_script.py lint</code> when this page is built.</p>', "lint")
    body += "\n" + sec("More scripts", example_list(examples, slug).replace("{P}", "../../"), "more-examples")
    body += ('\n<section class="ug-sec wrap"><p class="ug-small">Source: <a href="%s/blob/main/examples/scripts/%s.json" rel="noopener">%s.json</a>, '
             'with the sheet and prompts in <code>examples/rendered/</code> and <code>examples/prompts/</code> on GitHub. The CLI prints the same prompts: '
             '<code>python3 scripts/ugc_script.py prompts examples/scripts/%s.json --model all</code>.</p></section>' % (REPO, slug, slug, slug))
    return body


# ---------------------------------------------------------------- pages

CHOOSE_HTML = (
    "<p>Start from the product, not the platform, and keep the five beats whichever script you pick.</p>"
    '<ul class="ug-notes">'
    '<li>Software and apps need real screen recordings. Start from <a href="meal-prep-app/">Platefull</a> or <a href="b2b-invoicing/">Ledgerly</a>.</li>'
    '<li>A physical product with a visible result suits a demo with no presenter. Try <a href="cleaning-spray/">Clearstone</a> or <a href="kitchen-chopper/">Dicewell</a>.</li>'
    '<li>Skin, pet-health and other regulated categories need the most care with claims. <a href="skincare-serum/">Dewline</a> shows how to list each one with evidence.</li>'
    '<li>If a real customer speaks, use <a href="dog-dental-chews/">Bitewell</a>, the one script with a signed release.</li></ul>'
    "<p>Then swap the hook, check the claims list against what you can prove, and run lint before you generate anything.</p>")


def build_pages(examples, bad):
    pages = {}
    n_ex = len(examples)
    cats, avoid = parse_hooks()
    n_hooks = sum(len(h) for _, h in cats)
    faq = faq_items(n_hooks)
    default = "skincare-serum"
    cases = build_cases(copy.deepcopy(examples[default]))
    # every message the bad example and the cases can raise must have a stated reason
    rb = us.lint(bad)
    if (len(rb.errors), len(rb.warnings)) != (12, 8):
        raise SystemExit("bad example now gives %d errors and %d warnings" % (len(rb.errors), len(rb.warnings)))
    for _, m in rb.errors + rb.warnings:
        if not rule_for(m):
            raise SystemExit("no RULES entry explains: " + m)
    for c in cases:
        for m in c["all"]:
            if not rule_for(m):
                raise SystemExit("no RULES entry explains: " + m)
    for slug, b in examples.items():
        r = us.lint(b)
        if r.errors or r.warnings:
            raise SystemExit("example %s does not lint clean" % slug)
        if slug not in INTROS:
            raise SystemExit("no intro for " + slug)
    app = webapp()

    # ---- index
    data = {"order": list(examples), "titles": {s: b["title"] for s, b in examples.items()}, "badSlug": BAD_SLUG,
            "examples": examples, "bad": bad, "blank": us.scaffold("your vertical", "Your product"),
            "rules": [{"re": rx, "why": why} for rx, why in RULES], "cta": CTA, "repo": REPO,
            "friendly": [{"re": rx, "to": to} for rx, to in FRIENDLY], "pres": PRES_FRIENDLY,
            "pats": {"t": us.TESTIMONIAL_PATTERNS, "e": us.IMPERSONATION_PATTERNS}, "anchors": COMPLIANCE_ANCHORS}
    d0 = examples[default]
    r0 = us.lint(d0)
    legend = ('<p class="ug-legend">✕ Error: fix it before you use the script. ! Warning: check it. Image is the first frame for image-to-video. '
              'Kling and Veo 3 are the video prompts.</p>')
    builder = """<section class="ug-builder wrap" id="builder" aria-labelledby="builder-h">
<h2 id="builder-h">Script checker and prompt generator</h2>
<div class="ug-bar ug-bar-ok" id="ug-bar" aria-live="polite"><span>{bar}</span><a href="#ug-out">See result</a></div>
<div class="ug-layout">
<div class="ug-form" id="ug-form" data-example="{default}"><p class="ug-small">Loading the form. It needs JavaScript. The result on the right is a ready-made example.</p></div>
<div class="ug-out" id="ug-out">
<h3>Lint result</h3>
<div id="ug-lint">{lint}</div>
<h3>Stats</h3>
<div id="ug-stats">{stats}</div>
<h3>Prompts per shot</h3>
{legend}
<div class="ug-tabs" id="ug-tabs" role="tablist" aria-label="Prompt format">{tabs}</div>
<div id="ug-panel" role="tabpanel">{panel}</div>
</div>
</div>
<script type="application/json" id="ug-data">{data}</script>
</section>""".format(default=default, bar="✓ %s · %s" % (plural(len(r0.errors), "error"), plural(len(r0.warnings), "warning")), lint=lint_html(r0, d0), stats=stats_html(us.stats(d0)), legend=legend, tabs=tabs_html("kling"),
                     panel=panel_html(d0, "kling", default), data=jdump(data))
    steps = """<ol class="ug-steps">
<li><strong>Load or write a script</strong>Pick one of the %d examples, start from a blank script or edit the JSON. A script has a hook, five beats in order, captions, a voiceover and a source for every shot.</li>
<li><strong>Fix what lint flags</strong>Errors block the script: a missing AI label, a fake testimonial, an unlisted claim, a hook over 12 words. Warnings are worth a check: pace, caption length, the platform switch.</li>
<li><strong>Copy the prompts</strong>Each generated shot gets a first-frame image prompt, a Kling prompt and a Veo 3 prompt. Copy one shot or all of them, with the character count beside each.</li>
</ol>""" % n_ex
    body = builder
    body += "\n" + answer_sec("How does a UGC ad script checker work?", HOME_ANSWER)
    body += "\n" + sec("How it works", steps, "how")
    body += "\n" + sec("%d example scripts" % n_ex, example_list(examples).replace("{P}", ""), "examples")
    body += "\n" + faq_html(faq)
    desc = fit_desc([
        "Free UGC ad script checker: lint the hook, AI label, claims and fake testimonials, then copy Kling, Veo 3 and image prompts per shot. Open source.",
        "Free UGC ad script checker: lint the hook, AI label, claims and testimonials, then copy prompts for Kling, Veo 3 and images. Open source.",
        "Free UGC ad script checker: lint the hook, AI label and claims, then copy per-shot prompts for Kling and Veo 3. Open source."])
    pages[""] = page(
        "", fit_title(["UGC Ad Script Checker and Prompt Generator | UniqAI", "UGC Ad Script Checker | UniqAI"]), desc,
        "UGC ad script checker for Kling and Veo 3 prompts",
        "Edit a UGC ad script and lint it for the hook, the AI label, claims and fake testimonials. Then copy a prompt for every generated shot, for image models, Kling and Veo 3. "
        "Page views and button clicks are counted anonymously; your script text never leaves the browser.",
        body, examples, [("Checker", "")], current="", extra_graph=[app, faq_graph(faq)], main_entity=BASE + "#app",
        skip="#builder", skip_label="Skip to the checker", builder=True,
        buttons=[btn("#builder", "Open the checker"), btn("examples/", "See %d examples" % n_ex, ghost=True),
                 btn(utm("/models/kling", content="hero"), "Try Kling or Veo on UniqAI", ghost=True, out="hero")])

    # ---- examples index
    pages["examples/"] = page(
        "examples/", fit_title(["%d UGC Ad Script Examples with Kling and Veo 3 Prompts | UniqAI" % n_ex,
                                "%d UGC Ad Script Examples with Prompts | UniqAI" % n_ex]),
        fit_desc(["%d finished UGC ad scripts for skincare, apps, food, pets and travel, each with a hook, shot list, captions and Kling and Veo 3 prompts. Free, open source." % n_ex,
                  "%d finished UGC ad scripts for skincare, apps, food, pets and travel, with hooks, shot lists, captions and Kling and Veo 3 prompts. Open source." % n_ex,
                  "%d finished UGC ad scripts, each with a hook, shot list, captions and Kling and Veo 3 prompts. Free and open source." % n_ex]),
        "UGC ad script examples", "Each example is a complete script that passes lint: the hook, five beats, captions, voiceover, claims with evidence and prompts for every generated shot. "
        "Open one to read it, or load it into the checker and change it.",
        answer_sec("What does a finished UGC ad script look like?", EXAMPLES_ANSWER) + "\n"
        + sec("%d finished scripts" % n_ex, example_list(examples).replace("{P}", ""), "list") + "\n" + sec("How to choose a UGC ad script template", CHOOSE_HTML, "choose"), examples,
        [("Checker", ""), ("Examples", "examples/")], current="examples/", extra_graph=[app], main_entity=BASE + "#app",
        skip="#list", skip_label="Skip to the examples",
        buttons=[btn("../", "Open the checker"), btn(REPO + "/tree/main/examples", "Source files on GitHub", ghost=True)])

    # ---- example pages
    for slug, b in examples.items():
        path = "examples/%s/" % slug
        st = us.stats(b)
        pages[path] = page(
            path, example_title(slug, b), example_desc(slug, b, st), b["title"], INTROS[slug], example_body(slug, b, examples), examples,
            [("Checker", ""), ("Examples", "examples/"), (b["title"], path)], current="examples/", kind="WebPage",
            quote=b["hook"]["text"], skip="#prompts", skip_label="Skip to the prompts",
            buttons=[btn("../../?example=%s" % slug, "Open in checker"), btn("#prompts", "Jump to the prompts", ghost=True)])

    # ---- hooks
    cat_html = ""
    for name, hooks in cats:
        cid = re.sub(r"[^a-z]+", "-", name.lower()).strip("-")
        lis = "".join('<li><span class="ug-tag" title="%s">%s</span> %s</li>' % (esc(TAG_NAMES[t]), t, esc(x)) for t, x in hooks)
        cat_html += '<section class="ug-cat" aria-labelledby="cat-%s"><h3 id="cat-%s">%s</h3><ul class="ug-hooks">%s</ul></section>' % (
            cid, cid, esc(name), lis)
    legend_rows = [(t, TAG_NAMES[t]) for t in TAG_NAMES]
    avoid_html = '<ul class="ug-notes">%s</ul>' % "".join("<li>%s</li>" % md_inline(x) for x in avoid)
    body = answer_sec("What makes a UGC hook work in 2 seconds?", HOOK_ANSWER)
    body += "\n" + sec("Hook types", "<p>Each hook in the library carries one of these tags.</p>" + table(["Tag", "Type"], legend_rows), "types")
    body += "\n" + sec("%d hooks by product category" % n_hooks, cat_html, "library")
    body += "\n" + sec("Hooks to avoid", avoid_html, "avoid")
    body += "\n" + sec("Use a hook", '<p>Put the hook in <code>hook.text</code> and in the first caption, then lint the script. '
                       'The checker flags a hook over 12 words or a first shot that ends after 2 seconds.</p><p class="ug-actions">%s %s</p>' % (
                           btn("../", "Open the checker"), btn("../examples/", "See the examples", ghost=True)), "use")
    pages["hooks/"] = page(
        "hooks/", fit_title(["UGC Ad Hooks: %d Examples for the First 2 Seconds | UniqAI" % n_hooks, "%d UGC Ad Hooks for the First 2 Seconds | UniqAI" % n_hooks]),
        fit_desc(["%d UGC ad hooks of 12 words or fewer, grouped by product category and type, plus the openers to avoid. Free, open source, CC BY 4.0." % n_hooks,
                  "%d UGC ad hooks of 12 words or fewer, by product category and type, plus openers to avoid. Free and open source, CC BY 4.0." % n_hooks,
                  "%d UGC ad hooks of 12 words or fewer, by category and type. Free and open source, CC BY 4.0." % n_hooks]),
        "UGC ad hook library", "Hooks for the first two seconds of a UGC ad, grouped by product category. Each one is 12 words or fewer, written to be said and captioned at once, "
        "and works with a demonstrator presenter.", body, examples, [("Checker", ""), ("Hooks", "hooks/")], current="hooks/",
        extra_graph=[app], main_entity=BASE + "#app", skip="#answer", skip_label="Skip to the answer",
        buttons=[btn("../", "Open the checker"), btn(REPO + "/blob/main/references/hook-library.md", "Source on GitHub", ghost=True)])

    # ---- compliance
    def bl(items):
        return '<ul class="ug-notes">%s</ul>' % "".join("<li>%s</li>" % md_inline(i) for i in items)
    sections = [
        ("1. Disclose AI-generated content", bl([
            "Put a plain label in an on-screen caption from the first frame: `AI-generated ad`, or `AI-generated ad (b-roll)` when only some shots are generated. Lint fails if the label is missing from every caption while any shot is generated.",
            "Switch on the platform's AI-content setting when you upload (`compliance.platform_toggle`). TikTok, Meta and YouTube each have their own wording and scope, so read the live policy.",
            "EU: Regulation (EU) 2024/1689 (AI Act), Article 50, sets transparency duties for AI-generated or manipulated content that resembles real people, places or events, with application dates from August 2026.",
            "Do not hide the label in a corner the platform UI covers. A caption line is safest."]), "disclose"),
        ("2. Never fabricate testimonials", bl([
            "An AI presenter is a demonstrator. It can show, explain, compare and ask. It cannot say \"I've used this\", \"changed my life\", \"honest review\" or \"not sponsored\". Lint fails these phrasings for AI and no-presenter scripts.",
            "Star ratings, \"thousands of happy customers\", review screenshots and \"verified buyer\" wording are claims about real people. They need real sources and belong in `claims` with evidence, or stay out.",
            "US: the FTC rule on fake reviews and testimonials, 16 CFR Part 465 (effective 21 October 2024), prohibits testimonials by people who do not exist or who did not use the product, including AI-generated ones. The Endorsement Guides, 16 CFR Part 255 (revised 2023), require honest experience and disclosure of material connections.",
            "A real customer or creator can testify. Get a signed release, keep proof of purchase and disclose the paid relationship in speech and on screen."]), "testimonials"),
        ("3. Never impersonate people, creators or brands", bl([
            "`presenter.description` must describe a fictional person. Do not reference a real creator, actor, customer or employee, and do not use someone's photo as the start frame without written consent for this use.",
            "No platform or third-party endorsement wording such as \"as seen on TikTok\", \"Instagram approved\" or \"endorsed by\". Lint fails these.",
            "Keep logos, flags, carrier names, airline liveries and competitor packaging out of generated shots. The default negative prompt covers text, watermark and logos."]), "impersonation"),
        ("4. Claims need evidence", bl([
            "Anything that sounds measurable is a claim: \"clinically\", \"proven\", \"guaranteed\", \"#1\", \"dermatologist-tested\", percentages, \"results in N days\", \"lose N\". Each goes in `claims` with where it is proven. Lint fails unlisted ones and warns on unlisted numbers.",
            "Health, weight, skin-condition, financial-return and \"safe for\" wording are regulated categories on every major ad platform. Prefer observable demos to outcome promises.",
            "Prices and offers: say them only if they are true on the day and for the run of the ad. Otherwise write \"link below\".",
            "Before and after: a split screen that shows application is a demo. A split screen that implies a result over time is a claim and needs a source."]), "claims"),
    ]
    body = answer_sec("What does an AI-generated UGC ad have to disclose?", COMPLIANCE_ANSWER)
    body += "\n" + sec("Working checklist, not legal advice", '<p>These rules are a working checklist for ad drafts. Policies change, so read each platform\'s current ad policy before a campaign goes live. '
                       'The dates in the source file are from %s.</p><p class="ug-small">Source: <a href="%s" rel="noopener">references/compliance.md</a>.</p>' % (RULES_CHECKED, COMPLIANCE_URL), "scope")
    ftc = ('<p>As checked on %s, and summarised from <a href="%s" rel="noopener">references/compliance.md</a>: the FTC rule on fake reviews and testimonials, '
           '16 CFR Part 465 (effective 21 October 2024), prohibits testimonials by people who do not exist or who did not use the product, including AI-generated ones.</p>'
           '<ul class="ug-notes"><li>The FTC Endorsement Guides, 16 CFR Part 255 (revised 2023), require endorsements to reflect honest experience and material connections to be disclosed.</li>'
           '<li>An AI presenter is a demonstrator. It can show, explain, compare and ask, and it cannot say \u201cI\u2019ve used this\u201d or \u201cnot sponsored\u201d. Lint fails those phrasings for AI and no-presenter scripts.</li>'
           '<li>A real customer or creator can testify, with a signed release, proof of purchase and a paid-partnership disclosure in speech and on screen.</li></ul>'
           '<p class="ug-small">A working checklist, not legal advice. Read the rule text before you run a campaign.</p>') % (RULES_CHECKED, COMPLIANCE_URL)
    body += "\n" + sec("FTC rules for fake testimonials in AI UGC ads", ftc, "ftc")
    for t, inner, sid in sections:
        body += "\n" + sec(t, inner, sid)
    case_html = ""
    for c in cases:
        case_html += ('<article class="ug-case" id="rule-%s"><h3>%s</h3><p>%s</p>'
                      '<div class="ug-pair"><div class="ug-fail"><p><b>✕ Fails</b> <span class="ug-where">%s</span></p><pre tabindex="0">%s</pre>'
                      '<p class="ug-small"><b>%s:</b> %s</p></div>'
                      '<div class="ug-pass"><p><b>✓ Passes</b></p><pre tabindex="0">%s</pre></div></div></article>' % (
                          c["id"], esc(c["title"]), esc(c["why"]), esc(c["where"]), esc(c["fail"]), c["level"], esc(c["message"]), esc(c["pass"])))
    body += "\n" + sec("What lint checks, rule by rule", "<p>Each failing example below was run through lint when this page was built. The message is what the checker shows for it.</p>" + case_html, "rules")
    plat = [("TikTok Ads", "AI-generated content disclosure; restricted categories (health, finance, supplements); no misleading before and after; branded content toggle for creator posts; ad length limits for the placement."),
            ("Meta (Reels, Instagram, Facebook)", "\"AI info\" labelling and disclosure rules for realistic generated content; personal attributes policy (do not imply the viewer has a condition); restricted categories; special ad categories."),
            ("YouTube Shorts (Google Ads)", "Altered or synthetic content disclosure; misrepresentation policy; healthcare and medicines policy; Shorts length limits for the ad format.")]
    body += "\n" + sec("Platform ad-policy notes", table(["Platform", "What to check"], plat) + '<p class="ug-small">A summary. Verify against the live policy before launch.</p>', "platforms")
    pre = ["`python3 scripts/ugc_script.py lint script.json --strict` passes", "AI label visible in the first two seconds, not cropped by the platform UI",
           "Platform AI-content setting switched on at upload", "Presenter is fictional, or a real person with release and disclosure",
           "Every entry in `claims` has a document behind it, dated", "No real logos, people or competitor products in generated shots",
           "Offer and price in the CTA are true for the run dates", "Policy pages of each target platform re-read this quarter"]
    body += "\n" + sec("Pre-flight checklist", '<ul class="ug-checklist">%s</ul>' % "".join("<li>%s</li>" % md_inline(x) for x in pre), "preflight")
    pages["compliance/"] = page(
        "compliance/", fit_title(["AI UGC Ad Compliance Checklist: Labels and Claims | UniqAI", "AI UGC Ad Compliance Checklist | UniqAI"]),
        fit_desc(["A checklist for AI-generated UGC ads: AI label, fake testimonials, impersonation and claims, each with a lint fail and pass example. Not legal advice.",
                  "A checklist for AI-generated UGC ads: AI label, fake testimonials, impersonation and claims, with a fail and pass example. Not legal advice.",
                  "A checklist for AI-generated UGC ads: the AI label, fake testimonials and claims, with examples. Not legal advice."]),
        "AI-generated UGC ad compliance checklist",
        "What to disclose, what to avoid and what to prove in a UGC ad made with AI video. Every rule that lint enforces is shown below with a failing and a passing example.",
        body, examples, [("Checker", ""), ("Compliance", "compliance/")], current="compliance/", extra_graph=[app], main_entity=BASE + "#app",
        skip="#answer", skip_label="Skip to the answer",
        buttons=[btn("../", "Open the checker"), btn(COMPLIANCE_URL, "Source on GitHub", ghost=True)])
    return pages


def not_found():
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Page not found | UniqAI ugc-ad-director</title><meta name="robots" content="noindex"><link rel="icon" href="/ugc-ad-director/assets/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/ugc-ad-director/site.css"></head>
<body><main class="wrap" style="padding:20vh 24px"><h1>Page not found</h1><p class="lead">The UGC ad script checker is here: <a href="/ugc-ad-director/">UGC ad script checker</a>.</p></main></body></html>
"""


def llms_txt(examples, n_hooks):
    lines = ["# ugc-ad-director: UGC ad script checker (UniqAI, open source)", "",
             "> Free in-browser checker for UGC ad scripts. It lints the hook, the AI label, claims and fake testimonials, then renders a first-frame image prompt, a Kling prompt and a Veo 3 prompt for every generated shot. "
             "%d example scripts, %d hooks. Rules last checked %s; a working checklist, not legal advice. Code MIT, vocabulary CC BY 4.0. Curated by Vlad Yasinski (%s)." % (len(examples), n_hooks, RULES_CHECKED, LINKEDIN),
             "", "## Pages", "", "- [Script checker](%s): the in-browser tool, with an example loaded; it writes the AI UGC ad generator prompt for each shot" % BASE,
             "- [Examples](%sexamples/): %d finished scripts" % (BASE, len(examples)),
             "- [Hook library](%shooks/): %d hooks of 12 words or fewer, by product category" % (BASE, n_hooks),
             "- [Compliance rules](%scompliance/): FTC rules on fake testimonials in AI UGC ads, plus the AI label, impersonation and claims, each with a lint example" % BASE,
             "", "## Examples", ""]
    for slug, b in examples.items():
        lines.append("- [%s](%sexamples/%s/): hook \"%s\" (%s, %s)" % (b["title"], BASE, slug, b["hook"]["text"], us.fmt_s(b["duration_s"]), PRESENTER_LABEL[b["presenter"]["type"]]))
    lines += ["", "## Source", "",
              "- [Repository](%s): ugc_script.py (stdlib CLI), example scripts as JSON, vocabulary in references/" % REPO,
              "- [Compliance notes](%s): checked %s" % (COMPLIANCE_URL, RULES_CHECKED),
              "", "## Run a prompt", "",
              "- [UniqAI Kling](%s): run Kling prompts" % utm("/models/kling", medium="llms"),
              "- [UniqAI Veo](%s): run Veo 3 prompts" % utm("/models/veo", medium="llms"), ""]
    return "\n".join(lines)


def sitemap(paths):
    urls = "".join("<url><loc>%s%s</loc></url>" % (BASE, p) for p in paths)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>\n' % urls


def render_all():
    examples, bad = load_examples()
    pages = build_pages(examples, bad)
    cats, _ = parse_hooks()
    files = {p + "index.html": c for p, c in pages.items()}
    files["404.html"] = not_found()
    files["llms.txt"] = llms_txt(examples, sum(len(h) for _, h in cats))
    files["sitemap.xml"] = sitemap(list(pages))
    files["robots.txt"] = "User-agent: *\nAllow: /\n\nSitemap: %ssitemap.xml\n" % BASE
    files[".nojekyll"] = ""
    return files


def write_tree(out):
    for rel, content in render_all().items():
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    for dirpath, _, names in os.walk(STATIC):
        for n in names:
            src = os.path.join(dirpath, n)
            dst = os.path.join(out, os.path.relpath(src, STATIC))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
    og = os.path.join(out, "assets", "social-preview.png")
    os.makedirs(os.path.dirname(og), exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, "assets", "social-preview.png"), og)


def tree(root):
    out = {}
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = os.path.join(dirpath, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = f.read()
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="ug-site-")
    try:
        write_tree(tmp)
        new = tree(tmp)
        if "--check" in sys.argv:
            old = tree(OUT) if os.path.isdir(OUT) else {}
            stale = sorted(k for k in set(new) | set(old) if new.get(k) != old.get(k))
            if stale:
                print("docs/ is out of date, run: python3 scripts/site.py\n  " + "\n  ".join(stale))
                return 1
            print("docs/ up to date (%d files)" % len(new))
            return 0
        if os.path.isdir(OUT):
            shutil.rmtree(OUT)
        shutil.copytree(tmp, OUT)
        print("wrote %d files to docs/" % len(new))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
