import json, os, re, shutil, subprocess, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ugc_script as us  # noqa: E402
import site as _unused  # noqa: F401,E402  (stdlib site; the generator is imported by path below)
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("ug_site", os.path.join(ROOT, "scripts", "site.py"))
ug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ug)


class SiteTests(unittest.TestCase):
    def test_docs_up_to_date(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "site.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_page_count_and_seo(self):
        files = ug.render_all()
        pages = [k for k in files if k.endswith("index.html")]
        self.assertEqual(len(pages), 15)
        titles, descs = set(), set()
        for k in pages:
            h = files[k]
            t = re.search(r"<title>(.*?)</title>", h).group(1)
            d = re.search(r'name="description" content="(.*?)"', h).group(1)
            self.assertLessEqual(len(t), 60, (k, len(t)))
            self.assertTrue(100 <= len(d) <= 150, (k, len(d)))
            self.assertNotIn(t, titles)
            self.assertNotIn(d, descs)
            titles.add(t); descs.add(d)
            self.assertEqual(h.count('rel="canonical"'), 1)
            self.assertIn("assets/social-preview.png", h)
            self.assertEqual(("FAQPage" in h), k == "index.html")

    def test_answer_blocks_and_friendly_lint(self):
        files = ug.render_all()
        for k in ("index.html", "examples/index.html", "examples/skincare-serum/index.html"):
            self.assertRegex(files[k], r"<h2[^>]*>[^<]*\?</h2>", k)
        self.assertIn("How to choose a UGC ad script template", files["examples/index.html"])
        self.assertIn("FTC rules for fake testimonials in AI UGC ads", files["compliance/index.html"])
        bad = files["compliance/index.html"]
        self.assertNotRegex(bad, r"--[a-z]+-[a-z]+|\b(first_person|ai_presenter)\b")

    def test_uniq_links_tagged(self):
        for k, h in ug.render_all().items():
            for u in re.findall(r'href="(https://uniq\.app[^"]*)"', h):
                self.assertIn("utm_campaign=ugc-ad-director", u, k)

    def test_no_claim_that_uniqai_runs_other_models(self):
        models = r"(Wan|Seedance|Runway|Flux|Nano Banana|GPT|Gemini|Sora|Hailuo|Luma)"
        bad = [r"UniqAI[^.|]*\b(runs|offers|hosts|supports|has)\b[^.|]*\b%s\b" % models,
               r"\b%s\b[^.|]*\bon UniqAI\b" % models,
               r"\b(runs|offers|hosts)\b[^.|]*\b%s\b[^.|]*UniqAI" % models]
        for k, h in ug.render_all().items():
            if not (k.endswith(".html") or k.endswith(".txt")):
                continue
            text = re.sub(r"<[^>]+>", "|", h)
            for pat in bad:
                self.assertIsNone(re.search(pat, text), (k, pat))

    def test_no_bad_articles(self):
        for k, h in ug.render_all().items():
            self.assertIsNone(re.search(r"\ba (8|11|18)[- ]", h), k)
            self.assertNotIn("to to", h, k)

    def test_every_example_page_has_all_shots(self):
        files = ug.render_all()
        ex, _ = ug.load_examples()
        for slug, s in ex.items():
            h = files["examples/%s/index.html" % slug]
            for shot in s["shots"]:
                self.assertIn(ug.esc(shot["caption"].split("\n")[0]), h, (slug, shot.get("id")))

    def test_js_matches_python(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        r = subprocess.run([node, os.path.join(ROOT, "tests", "parity.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        ex, bad = ug.load_examples()
        scripts = dict(ex)
        scripts[ug.BAD_SLUG] = bad
        for slug, s in scripts.items():
            rep = us.lint(s)
            j = got[slug]
            self.assertEqual(j["lint"]["errors"], [list(e) for e in rep.errors], slug)
            self.assertEqual(j["lint"]["warnings"], [list(w) for w in rep.warnings], slug)
            self.assertEqual(j["stats"], us.stats(s), slug)
            self.assertEqual(j["lintHtml"], ug.lint_html(rep, s, "../../"), slug)
            for k in ("image", "kling", "veo3", "all"):
                self.assertEqual(j["prompts"][k], us.render_prompts(s, k), (slug, k))
        self.assertEqual(len(scripts), 12)
        self.assertEqual((len(got[ug.BAD_SLUG]["lint"]["errors"]), len(got[ug.BAD_SLUG]["lint"]["warnings"])), (12, 8))


if __name__ == "__main__":
    unittest.main()
