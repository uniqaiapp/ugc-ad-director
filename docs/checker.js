/* UGC script checker: a port of lint, stats and the prompt functions in scripts/ugc_script.py, plus the page UI.
   tests/parity.js runs this over every example and the failing one and compares with the Python output.
   Nothing you type leaves the page: tracking sends action names and counts only. */
(function () {
  "use strict";

  var D = null; // data embedded by scripts/site.py
  var BEATS = ["hook", "problem", "demo", "proof", "cta"];
  var PRESENTERS = ["ai_generated", "real_person", "none"];
  var SOURCES = ["image-to-video", "text-to-video", "screen-recording", "live-footage", "still"];
  var HOOK_MAX_S = 2.0, HOOK_MAX_WORDS = 12, CAPTION_MAX_CHARS = 48, DEFAULT_MAX_WPS = 3.5;
  var TESTIMONIAL = [
    "\\bi(?:'ve| have) (?:been )?(?:using|used|tried|owned)\\b",
    "\\bi (?:bought|ordered|tried|use|used|switched)\\b",
    "\\bmy (?:results?|skin|hair|dog|cat|team|kids?|husband|wife) (?:is|are|was|were|has|have|got)\\b",
    "\\bchanged my life\\b",
    "\\bas a (?:customer|real customer|verified buyer|user)\\b",
    "\\bverified (?:buyer|purchase|review)\\b",
    "\\b(?:honest|real|genuine) review\\b",
    "\\bnot sponsored\\b",
    "\\bi'?m not (?:paid|sponsored)\\b"
  ];
  var CLAIMS = [
    "\\bclinically\\b", "\\bproven\\b", "\\bguarantee[ds]?\\b", "\\bcures?\\b", "\\bdoctor[- ]recommended\\b",
    "\\bdermatologist[- ](?:recommended|tested|approved)\\b", "\\b#\\s?1\\b", "\\bbest[- ]selling\\b",
    "\\bfda[- ](?:approved|cleared)\\b", "\\bscientifically\\b", "\\bresults? in \\d+ (?:days?|weeks?|hours?)\\b",
    "\\blose \\d+", "\\b\\d+\\s?%", "\\b\\d+x\\b", "\\baward[- ]winning\\b", "\\bsafe for\\b", "\\bno side effects\\b"
  ];
  var IMPERSONATION = [
    "\\bas seen on\\b", "\\b(?:tiktok|instagram|youtube|meta) (?:recommends|approved|official)\\b",
    "\\bofficial (?:tiktok|instagram|youtube) partner\\b", "\\bendorsed by\\b"
  ];
  var NUMBER_SRC = "(?<![\\p{L}\\p{N}_$])\\d[\\d,.]*\\s?(?:%|x|k|m|days?|weeks?|hours?|minutes?|lbs?|kg|years?)?\\b";

  // ---------------------------------------------------------------- Python behaviour helpers

  function has(o, k) { return o !== null && typeof o === "object" && Object.prototype.hasOwnProperty.call(o, k); }
  function get(o, k, d) { return has(o, k) ? o[k] : d; }
  function truthy(v) {
    if (v === null || v === undefined || v === false || v === 0 || v === "") return false;
    if (Array.isArray(v)) return v.length > 0;
    if (typeof v === "object") return Object.keys(v).length > 0;
    return true;
  }
  function or(a, b) { return truthy(a) ? a : b; }
  function pyStr(v) {
    if (v === null || v === undefined) return "None";
    if (v === true) return "True";
    if (v === false) return "False";
    return String(v);
  }
  function fmtG(x) { return String(Number(Number(x).toPrecision(6))); } // Python "{x:g}"
  function fmtS(x) { return fmtG(x) + "s"; }
  function strip(s) { return String(s).trim(); }
  function rstripDots(s) { return String(s).replace(/\.+$/, ""); }
  function len(s) { return Array.from(String(s)).length; }
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
  }
  // Python round-half-even on the exact binary value: "{x:.Nf}" and round(x, N).
  function pyFixed(x, f) {
    var s = Math.abs(x).toFixed(40), parts = s.split("."), ip = parts[0], fp = parts[1];
    var keep = fp.slice(0, f), rest = fp.slice(f), digits = ip + keep, up = false;
    if (rest.charAt(0) > "5") up = true;
    else if (rest.charAt(0) === "5") {
      if (/[1-9]/.test(rest.slice(1))) up = true;
      else up = Number(digits.slice(-1)) % 2 === 1;
    }
    var n = BigInt(digits);
    if (up) n += 1n;
    var t = n.toString();
    while (t.length < f + 1) t = "0" + t;
    var out = f ? t.slice(0, t.length - f) + "." + t.slice(t.length - f) : t;
    return (x < 0 ? "-" : "") + out;
  }
  function pyRepr(x) { return Number.isInteger(x) ? x.toFixed(1) : String(x); } // float repr
  function round2(x) { return Number(pyFixed(x, 2)); }

  function words(text) {
    var m = String(text === null || text === undefined ? "" : text).match(/[A-Za-z0-9'’]+/g);
    return m ? m.length : 0;
  }
  function shotText(s) { return ["caption", "voiceover", "visual"].map(function (k) { return pyStr(get(s, k, "")); }).join(" "); }
  function anyMatch(patterns, text) {
    var low = String(text === null || text === undefined ? "" : text).toLowerCase();
    return patterns.filter(function (p) { return new RegExp(p).test(low); });
  }

  // ---------------------------------------------------------------- lint

  function lint(script, maxWps) {
    if (maxWps === undefined || maxWps === null) maxWps = DEFAULT_MAX_WPS;
    var errors = [], warnings = [];
    function E(w, m) { errors.push([w, m]); }
    function W(w, m) { warnings.push([w, m]); }
    ["title", "vertical", "product", "duration_s", "presenter", "hook", "shots", "cta", "compliance"].forEach(function (k) {
      if (!has(script, k)) E("script", "missing top-level field '" + k + "'");
    });
    if (errors.length) return { errors: errors, warnings: warnings };

    var duration = script.duration_s;
    var presenter = get(script.presenter, "type", null);
    if (PRESENTERS.indexOf(presenter) < 0) E("presenter", "type must be one of " + PRESENTERS.join(", "));
    if (duration > 60) W("duration", "over 60 s: check the current length limits of each target platform");

    var shots = script.shots;
    if (!shots.length) { E("shots", "no shots"); return { errors: errors, warnings: warnings }; }

    var hook = script.hook, hookText = get(hook, "text", "");
    if (!strip(hookText)) E("hook", "hook text is empty");
    if (words(hookText) > HOOK_MAX_WORDS) {
      E("hook", "hook is " + words(hookText) + " words; keep it under " + (HOOK_MAX_WORDS + 1) + " so it lands inside " + fmtG(HOOK_MAX_S) + " s");
    }
    var first = shots[0];
    if (get(first, "beat", null) !== "hook") E("shot 1", "first shot must be the hook");
    if (get(first, "start_s", 0) !== 0) E("shot 1", "first shot must start at 0");
    if (get(first, "end_s", 99) > HOOK_MAX_S) {
      E("shot 1", "hook shot ends at " + fmtS(get(first, "end_s", 0)) + "; it must end by " + fmtS(HOOK_MAX_S));
    }
    var core = String(hookText).toLowerCase().replace(/[.!?]+$/, "");
    if (core && truthy(get(first, "caption", null)) && String(first.caption).toLowerCase().indexOf(core) < 0 &&
        String(or(get(first, "voiceover", null), "")).toLowerCase().indexOf(core) < 0) {
      W("shot 1", "hook text does not appear in the first shot's caption or voiceover");
    }

    var seen = shots.map(function (s) { return get(s, "beat", null); });
    var order = BEATS.filter(function (b) { return seen.indexOf(b) >= 0; });
    if (order.join() !== BEATS.join()) {
      var missing = BEATS.filter(function (b) { return seen.indexOf(b) < 0; });
      E("beats", missing.length ? "missing beat(s): " + missing.join(", ") : "beats present");
    }
    var compact = seen.filter(function (b, i) { return i === 0 || seen[i - 1] !== b; });
    var a1 = compact.filter(function (b) { return BEATS.indexOf(b) >= 0; });
    var a2 = BEATS.filter(function (b) { return compact.indexOf(b) >= 0; });
    if (a1.join() !== a2.join()) E("beats", "beats out of order: " + seen.map(pyStr).join(" > ") + "; expected " + BEATS.join(" > "));
    if (get(shots[shots.length - 1], "beat", null) !== "cta") E("shots", "last shot must be the cta");

    var prevEnd = 0;
    shots.forEach(function (s, idx) {
      var where = "shot " + (idx + 1);
      var miss = ["beat", "start_s", "end_s", "visual", "caption", "source"].filter(function (k) { return !has(s, k); });
      if (miss.length) {
        E(where, "missing field(s): " + miss.join(", "));
        prevEnd = get(s, "end_s", prevEnd);
        return;
      }
      if (s.start_s !== prevEnd) E(where, "starts at " + fmtS(s.start_s) + " but previous shot ended at " + fmtS(prevEnd));
      if (s.end_s <= s.start_s) E(where, "end_s must be greater than start_s");
      prevEnd = s.end_s;
      if (SOURCES.indexOf(s.source) < 0) E(where, "source must be one of " + SOURCES.join(", "));
      if ((s.source === "image-to-video" || s.source === "text-to-video") && !truthy(get(s, "prompt", null))) {
        E(where, "AI-generated shot has no 'prompt' object");
      }
      if (truthy(get(s, "prompt", null))) {
        ["subject", "action", "camera"].forEach(function (k) {
          if (!truthy(get(s.prompt, k, null))) E(where, "prompt is missing '" + k + "'");
        });
      }
      pyStr(get(s, "caption", "")).split("\n").forEach(function (line) {
        if (len(line) > CAPTION_MAX_CHARS) {
          W(where, "caption line is " + len(line) + " chars; keep lines under " + (CAPTION_MAX_CHARS + 1) + " for a phone screen");
        }
      });
      var length = s.end_s - s.start_s;
      var vo = or(get(s, "voiceover", null), "");
      if (truthy(vo) && length > 0) {
        var wps = words(vo) / length;
        if (wps > maxWps) {
          W(where, "voiceover runs at " + pyFixed(wps, 1) + " words/s over " + fmtS(length) + "; above --max-wps " + fmtG(maxWps));
        }
      }
      if (presenter !== "real_person") {
        var hits = anyMatch(TESTIMONIAL, pyStr(get(s, "voiceover", "")).replace(/^None$/, "") + " " + pyStr(get(s, "caption", "")));
        if (hits.length) {
          E(where, "reads as a first-person customer testimonial but the presenter is '" + pyStr(presenter) +
            "': rewrite as a demo or a sourced claim");
        }
      }
      if (anyMatch(IMPERSONATION, shotText(s)).length) E(where, "implies a platform or third-party endorsement; remove it");
    });
    if (prevEnd !== duration) E("timeline", "shots end at " + fmtS(prevEnd) + " but duration_s is " + fmtS(duration));

    var claims = get(script, "claims", []);
    var claimTexts = claims.map(function (c) { return String(get(c, "text", "")).toLowerCase(); });
    claims.forEach(function (c, i) {
      if (!strip(or(get(c, "evidence", null), ""))) {
        E("claim " + (i + 1), "'" + get(c, "text", "") + "' has no evidence; add a source, a test or remove the claim");
      }
    });
    shots.forEach(function (s, idx) {
      var text = or(get(s, "voiceover", null), "") + " " + or(get(s, "caption", null), "");
      anyMatch(CLAIMS, text).forEach(function (pat) {
        var frag = new RegExp(pat).exec(text.toLowerCase())[0];
        if (!claimTexts.some(function (ct) { return ct.indexOf(frag) >= 0; })) {
          E("shot " + (idx + 1), "'" + frag + "' is a claim; list it under 'claims' with evidence or drop it");
        }
      });
      var re = new RegExp(NUMBER_SRC, "giu"), m;
      while ((m = re.exec(text)) !== null) {
        if (m[0] === "") { re.lastIndex++; continue; }
        var token = m[0].trim();
        if (/^\d{1,2}$/.test(token)) continue;
        if (!claimTexts.some(function (ct) { return ct.indexOf(token.toLowerCase()) >= 0; })) {
          W("shot " + (idx + 1), "number '" + token + "' is not backed by an entry in 'claims'");
        }
      }
    });

    var comp = script.compliance;
    var label = strip(or(get(comp, "ai_label", null), ""));
    var generated = shots.some(function (s) { return s.source === "image-to-video" || s.source === "text-to-video"; });
    if (presenter === "ai_generated" || generated) {
      if (!label) E("compliance", "ai_label is empty but the ad contains AI-generated footage or an AI presenter");
      else if (!shots.some(function (s) { return String(or(get(s, "caption", null), "")).toLowerCase().indexOf(label.toLowerCase()) >= 0; })) {
        E("compliance", "ai_label '" + label + "' does not appear in any caption; put it on screen");
      }
      if (!truthy(get(comp, "platform_toggle", null))) {
        W("compliance", "set platform_toggle: true once the AI-generated content switch is on in the ad manager");
      }
    }
    if (presenter === "ai_generated" && !strip(or(get(script.presenter, "description", null), ""))) {
      W("presenter", "describe the AI presenter so it cannot be mistaken for a specific real person");
    }
    if (!strip(or(get(script.cta, "text", null), ""))) E("cta", "cta text is empty");
    return { errors: errors, warnings: warnings };
  }

  // ---------------------------------------------------------------- stats

  function stats(script) {
    var shots = script.shots;
    var voWords = shots.reduce(function (a, s) { return a + words(get(s, "voiceover", null)); }, 0);
    var capLines = [];
    shots.forEach(function (s) {
      pyStr(get(s, "caption", "")).split("\n").forEach(function (ln) { if (ln) capLines.push(ln); });
    });
    var ai = shots.filter(function (s) { return s.source === "image-to-video" || s.source === "text-to-video"; });
    var longest = 0;
    capLines.forEach(function (ln) { longest = Math.max(longest, len(ln)); });
    return {
      shots: shots.length,
      duration_s: script.duration_s,
      hook_ends_s: get(shots[0], "end_s", null),
      hook_words: words(get(script.hook, "text", null)),
      voiceover_words: voWords,
      voiceover_wps: truthy(script.duration_s) ? round2(voWords / script.duration_s) : 0,
      caption_lines: capLines.length,
      longest_caption_line: longest,
      ai_generated_shots: ai.length,
      claims: get(script, "claims", []).length
    };
  }

  // ---------------------------------------------------------------- prompts

  function join() {
    var out = [];
    for (var i = 0; i < arguments.length; i++) {
      var p = arguments[i];
      if (truthy(p) && strip(p)) out.push(rstripDots(strip(p)));
    }
    return out.join(", ");
  }
  function promptOf(shot) { return get(shot, "prompt", {}) || {}; }

  function promptImage(shot, script) {
    var p = promptOf(shot);
    var style = or(get(p, "style", null), or(get(script, "style", null), "candid phone video still, natural light, realistic skin texture"));
    return join(get(p, "subject", null), get(p, "setting", null), get(p, "lighting", null), style, "vertical 9:16, sharp focus, no text or logos") + ".";
  }
  function promptKling(shot, script) {
    var p = promptOf(shot), length = shot.end_s - shot.start_s, clip = length <= 5 ? 5 : 10;
    var lines = [
      get(shot, "source", null) === "image-to-video" ? "[" + clip + "s clip, 9:16, image-to-video from the still above]" : "[" + clip + "s clip, 9:16, text-to-video]",
      join(get(p, "subject", null), get(p, "action", null)) + ".",
      "Camera: " + pyStr(get(p, "camera", "static handheld")) + "."
    ];
    if (truthy(get(p, "lighting", null))) lines.push("Lighting: " + p.lighting + ".");
    if (truthy(get(p, "style", null)) || truthy(get(script, "style", null))) lines.push("Style: " + or(get(p, "style", null), script.style) + ".");
    lines.push("Negative: " + or(get(p, "negative", null), "text, watermark, extra fingers, warped product, plastic skin, slow motion") + ".");
    return lines.join("\n");
  }
  function promptVeo3(shot, script) {
    var p = promptOf(shot);
    var lines = [
      join(get(p, "subject", null), get(p, "setting", null)) + ".",
      "Action: " + pyStr(get(p, "action", "")) + ".",
      "Camera: " + pyStr(get(p, "camera", "static handheld")) + "."
    ];
    if (truthy(get(p, "lighting", null))) lines.push("Lighting: " + p.lighting + ".");
    if (truthy(get(shot, "voiceover", null)) && get(script.presenter, "type", null) !== "none" && truthy(get(shot, "on_camera_speech", true))) {
      lines.push('Dialogue (on camera, natural pace): "' + shot.voiceover + '"');
    }
    if (truthy(get(p, "audio", null))) lines.push("Audio: " + p.audio + ".");
    lines.push("Style: " + or(get(p, "style", null), or(get(script, "style", null), "UGC phone footage, realistic, no on-screen text")) + ".");
    lines.push("No subtitles, no captions, no logos in frame.");
    return lines.join("\n");
  }
  var MODELS = { image: promptImage, kling: promptKling, veo3: promptVeo3 };
  var MODEL_NAMES = ["image", "kling", "veo3"];

  function isGenerated(s) { return s.source === "image-to-video" || s.source === "text-to-video" || truthy(get(s, "prompt", null)); }

  // [{i, id, beat, start, end, text}] for one model, the shots that model renders
  function shotPrompts(script, model) {
    var out = [];
    script.shots.forEach(function (s, i) {
      if (!isGenerated(s)) return;
      if (model === "image" && get(s, "source", null) !== "image-to-video") return;
      out.push({ i: i, id: has(s, "id") ? s.id : i + 1, beat: pyStr(get(s, "beat", "")), start: s.start_s, end: s.end_s, text: MODELS[model](s, script) });
    });
    return out;
  }
  function renderPrompts(script, model) {
    var names = model === "all" ? MODEL_NAMES : [model], out = [];
    script.shots.forEach(function (s) {
      if (!isGenerated(s)) return;
      names.forEach(function (name) {
        if (name === "image" && get(s, "source", null) !== "image-to-video") return;
        out.push("## shot " + s.id + " · " + s.beat + " · " + fmtS(s.start_s) + "-" + fmtS(s.end_s) + " · " + name);
        out.push(MODELS[name](s, script));
        out.push("");
      });
    });
    return out.join("\n").replace(/\s+$/, "") + "\n";
  }

  // ---------------------------------------------------------------- output markup (same as site.py out_html)

  function plural(n, w) { return n + " " + w + (n === 1 ? "" : "s"); }
  function whyFor(msg) {
    var rules = D.rules;
    for (var i = 0; i < rules.length; i++) if (new RegExp(rules[i].re).test(msg)) return rules[i].why;
    return "";
  }
  function lintHtml(r) {
    var ok = !r.errors.length;
    var h = '<p class="ug-sum ' + (ok ? "ug-sum-ok" : "ug-sum-bad") + '"><b>' + (ok ? "✓ Lint passed" : "✕ Lint failed") + "</b> <span>" +
      plural(r.errors.length, "error") + " · " + plural(r.warnings.length, "warning") + "</span></p>";
    var items = "";
    function item(cls, tag, label, pair) {
      var why = whyFor(pair[1]);
      items += '<li class="' + cls + '"><b aria-label="' + label + '">' + tag + " " + label + '</b> <span class="ug-where">' + esc(pair[0]) +
        "</span> " + esc(pair[1]) + (why ? ' <small class="ug-why">Why: ' + esc(why) + "</small>" : "") + "</li>";
    }
    r.errors.forEach(function (p) { item("ug-err", "✕", "Error", p); });
    r.warnings.forEach(function (p) { item("ug-warn", "!", "Warning", p); });
    if (items) h += '<ul class="ug-checks">' + items + "</ul>";
    return h;
  }
  function statsHtml(st) {
    var rows = [
      ["Hook ends at", st.hook_ends_s === null ? "not set" : fmtS(st.hook_ends_s)],
      ["Hook words", st.hook_words + " (limit " + HOOK_MAX_WORDS + ")"],
      ["Shots", String(st.shots)],
      ["Voiceover words", String(st.voiceover_words)],
      ["Words per second", pyRepr(st.voiceover_wps) + " (ceiling " + DEFAULT_MAX_WPS + ")"],
      ["Caption lines", st.caption_lines + ", longest " + st.longest_caption_line + " chars (limit " + CAPTION_MAX_CHARS + ")"],
      ["AI-generated shots", String(st.ai_generated_shots)],
      ["Claims listed", String(st.claims)]
    ];
    return '<div class="ug-tablewrap"><table class="ug-table ug-kv"><tbody>' + rows.map(function (r) {
      return '<tr><th scope="row">' + esc(r[0]) + "</th><td>" + esc(r[1]) + "</td></tr>";
    }).join("") + "</tbody></table></div>";
  }
  var TAB_LABELS = { image: "Image", kling: "Kling", veo3: "Veo 3" };
  function tabsHtml(model) {
    return MODEL_NAMES.map(function (k) {
      return '<button type="button" role="tab" class="ug-tab" data-model="' + k + '" aria-selected="' + (k === model ? "true" : "false") +
        '" tabindex="' + (k === model ? 0 : -1) + '">' + TAB_LABELS[k] + "</button>";
    }).join("");
  }
  function ctaHtml(model) {
    var c = D.cta[model];
    return '<div class="ug-cta"><p>' + esc(c.text) + '</p><a class="btn btn-ghost" href="' + esc(c.url) + '" data-out="checker-' + model + '">' + esc(c.label) + "</a></div>";
  }
  function panelHtml(script, model, slug) {
    var list = shotPrompts(script, model), h = "";
    if (!list.length) {
      h += '<p class="ug-small">' + (model === "image"
        ? "No image-to-video shots in this script, so there is no first-frame prompt to write."
        : "No AI-generated shots in this script, so there is nothing to prompt for.") + "</p>";
    }
    var all = [];
    list.forEach(function (it) {
      var pid = "ug-p-" + model + "-" + it.i;
      all.push("## shot " + it.id + " · " + it.beat + " · " + fmtS(it.start) + "-" + fmtS(it.end) + "\n" + it.text);
      h += '<div class="ug-shotp"><div class="ug-row"><h4>Shot ' + esc(it.id) + " · " + esc(it.beat) + " · " + fmtS(it.start) + "-" + fmtS(it.end) +
        '</h4><span class="ug-count">' + len(it.text) + ' characters</span></div><pre id="' + pid + '" tabindex="0">' + esc(it.text) +
        '</pre><p class="ug-actions"><button type="button" class="btn btn-sm btn-copy" data-copy="' + pid + '" data-kind="prompt" data-model="' + model +
        '" data-example="' + esc(slug || "custom") + '" data-label="Copy prompt">Copy prompt</button></p></div>';
    });
    if (list.length > 1) {
      h += '<pre id="ug-all-' + model + '" hidden>' + esc(all.join("\n\n")) + '</pre><p class="ug-actions"><button type="button" class="btn btn-sm btn-ghost btn-copy" data-copy="ug-all-' +
        model + '" data-kind="prompt" data-model="' + model + '" data-example="' + esc(slug || "custom") + '" data-label="Copy all ' + list.length +
        ' prompts">Copy all ' + list.length + " prompts</button></p>";
    }
    return h + ctaHtml(model);
  }

  // ---------------------------------------------------------------- UI

  var $ = function (id) { return document.getElementById(id); };
  var state = { script: null, slug: "", model: "kling", json: false };
  var lastKey = "", renderTimer = null;

  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function track(event, props) { if (window.ugTrack) window.ugTrack(event, props); }
  function say(msg) { var s = $("ug-status"); if (s) s.textContent = msg || ""; }

  function ta(attrs, label, value, rows) {
    return '<label class="ug-f"><span>' + esc(label) + "</span><textarea rows=\"" + (rows || 2) + '" ' + attrs + ">" + esc(value === undefined || value === null ? "" : value) + "</textarea></label>";
  }
  function num(attrs, label, value) {
    return '<label class="ug-f"><span>' + esc(label) + '</span><input type="number" step="any" min="0" ' + attrs + ' value="' + esc(value === undefined || value === null ? "" : value) + '"></label>';
  }
  function sel(attrs, label, options, value) {
    return '<label class="ug-f"><span>' + esc(label) + "</span><select " + attrs + ">" + options.map(function (o) {
      return '<option value="' + esc(o) + '"' + (o === value ? " selected" : "") + ">" + esc(o) + "</option>";
    }).join("") + "</select></label>";
  }
  function pv(path) {
    var cur = state.script;
    path.split(".").forEach(function (k) { cur = cur && typeof cur === "object" ? cur[k] : undefined; });
    return cur;
  }

  var PROMPT_FIELDS = [["subject", "Subject"], ["action", "Action"], ["camera", "Camera"], ["setting", "Setting"], ["lighting", "Lighting"], ["audio", "Audio (Veo 3)"], ["style", "Style"], ["negative", "Negative"]];

  function shotHtml(s, i) {
    var p = promptOf(s), a = 'data-shot="' + i + '" data-f="';
    var h = '<div class="ug-shotf"><div class="ug-shot-head"><strong>Shot ' + (i + 1) + '</strong><button type="button" class="ug-rm" data-act="rm-shot" data-i="' + i +
      '" aria-label="Remove shot ' + (i + 1) + '">&times;</button></div><div class="ug-three">' +
      sel(a + 'beat"', "Beat", BEATS, s.beat) + num(a + 'start_s"', "Start, seconds", s.start_s) + num(a + 'end_s"', "End, seconds", s.end_s) + "</div>" +
      sel(a + 'source"', "Source", SOURCES, s.source) +
      ta(a + 'caption"', "Caption (on screen)", s.caption, 2) + ta(a + 'voiceover"', "Voiceover", s.voiceover, 2) + ta(a + 'visual"', "Visual (what the viewer sees)", s.visual, 2) +
      '<details><summary>Prompt fields for AI-generated shots</summary><div class="ug-two">' +
      PROMPT_FIELDS.map(function (f) { return ta(a + "prompt." + f[0] + '"', f[1], p[f[0]], 2); }).join("") + "</div></details></div>";
    return h;
  }
  function claimHtml(c, i) {
    return '<div class="ug-claimf"><div class="ug-two">' + ta('data-claim="' + i + '" data-f="text"', "Claim " + (i + 1), c.text, 2) +
      ta('data-claim="' + i + '" data-f="evidence"', "Evidence for claim " + (i + 1), c.evidence, 2) + '</div><button type="button" class="ug-rm" data-act="rm-claim" data-i="' + i +
      '" aria-label="Remove claim ' + (i + 1) + '">&times;</button></div>';
  }
  function formHtml() {
    var sc = state.script, ex = D.order.map(function (s) { return [s, D.titles[s]]; });
    var opts = '<option value="">Custom (edit any field)</option>' + ex.map(function (e) {
      return '<option value="' + e[0] + '"' + (state.slug === e[0] ? " selected" : "") + ">" + esc(e[1]) + "</option>";
    }).join("") + '<option value="' + D.badSlug + '"' + (state.slug === D.badSlug ? " selected" : "") + ">Failing example: fake testimonial</option>";
    var pres = get(sc, "presenter", {}), hook = get(sc, "hook", {}), cta = get(sc, "cta", {}), comp = get(sc, "compliance", {});
    return '<fieldset><legend>Start</legend><label class="ug-f"><span>Load an example</span><select id="ug-example">' + opts + "</select></label>" +
      '<div class="ug-btns"><button type="button" class="btn btn-sm btn-ghost" id="ug-blank">Start from a blank script</button>' +
      '<button type="button" class="btn btn-sm btn-ghost" id="ug-jsonbtn" aria-pressed="false">Edit as JSON</button></div>' +
      '<p class="ug-status" id="ug-status" role="status" aria-live="polite"></p></fieldset>' +
      '<div id="ug-fields"><fieldset><legend>Product</legend>' + ta('data-path="title"', "Title", sc.title, 1) +
      '<div class="ug-two">' + ta('data-path="product.name"', "Product", get(sc.product, "name", ""), 1) + ta('data-path="vertical"', "Vertical", sc.vertical, 1) + "</div>" +
      ta('data-path="product.one_liner"', "One-line description", get(sc.product, "one_liner", ""), 2) + "</fieldset>" +
      '<fieldset><legend>Length and presenter</legend><div class="ug-two">' + num('data-path="duration_s"', "Duration, seconds", sc.duration_s) +
      sel('data-path="presenter.type"', "Presenter", PRESENTERS, get(pres, "type", "")) + "</div>" +
      ta('data-path="presenter.description"', "Presenter description (a fictional person)", get(pres, "description", ""), 2) + "</fieldset>" +
      '<fieldset><legend>Hook (first two seconds)</legend>' + ta('data-path="hook.text"', "Hook text, 12 words or fewer", get(hook, "text", ""), 2) +
      '<div class="ug-two">' + ta('data-path="hook.type"', "Hook type", get(hook, "type", ""), 1) + ta('data-path="hook.why"', "Why it works", get(hook, "why", ""), 1) + "</div></fieldset>" +
      '<fieldset><legend>Shots</legend><p class="ug-small">Each shot needs a beat, a start and an end time, a caption, a visual and a source. Times must run on without gaps.</p>' +
      sc.shots.map(shotHtml).join("") + '<div class="ug-btns"><button type="button" class="btn btn-sm btn-ghost" data-act="add-shot">Add a shot</button></div></fieldset>' +
      '<fieldset><legend>Claims and evidence</legend><p class="ug-small">Anything measurable that the script says goes here with where it is proven.</p>' +
      get(sc, "claims", []).map(claimHtml).join("") + '<div class="ug-btns"><button type="button" class="btn btn-sm btn-ghost" data-act="add-claim">Add a claim</button></div></fieldset>' +
      '<fieldset><legend>Call to action and AI label</legend>' + ta('data-path="cta.text"', "Spoken call to action", get(cta, "text", ""), 2) +
      ta('data-path="cta.on_screen"', "On-screen call to action", get(cta, "on_screen", ""), 1) +
      ta('data-path="compliance.ai_label"', "AI label shown in a caption", get(comp, "ai_label", ""), 1) +
      '<label class="ug-check"><input type="checkbox" data-path="compliance.platform_toggle"' + (truthy(get(comp, "platform_toggle", false)) ? " checked" : "") +
      "> The platform's AI-content switch is on in the ad manager</label></fieldset></div>" +
      '<label class="ug-f" id="ug-jsonwrap" hidden><span>Script JSON</span><textarea id="ug-json" rows="22" spellcheck="false"></textarea></label>';
  }

  function setPath(obj, path, value, remove) {
    var keys = path.split("."), cur = obj;
    for (var i = 0; i < keys.length - 1; i++) {
      if (!cur[keys[i]] || typeof cur[keys[i]] !== "object") cur[keys[i]] = {};
      cur = cur[keys[i]];
    }
    var last = keys[keys.length - 1];
    if (remove) delete cur[last]; else cur[last] = value;
  }
  function fieldValue(el) {
    if (el.type === "checkbox") return { v: el.checked };
    if (el.type === "number") {
      if (el.value.trim() === "") return { remove: true };
      var n = Number(el.value);
      return isNaN(n) ? null : { v: n };
    }
    return { v: el.value };
  }
  function onInput(e) {
    var el = e.target, fv = el.type ? fieldValue(el) : null;
    if (!fv || !el.getAttribute) return;
    var sc = state.script;
    if (el.hasAttribute("data-path")) setPath(sc, el.getAttribute("data-path"), fv.v, fv.remove);
    else if (el.hasAttribute("data-shot")) {
      var s = sc.shots[Number(el.getAttribute("data-shot"))], f = el.getAttribute("data-f");
      if (f.indexOf("prompt.") === 0) {
        var k = f.slice(7);
        if (!s.prompt || typeof s.prompt !== "object") s.prompt = {};
        if (fv.v === "") delete s.prompt[k]; else s.prompt[k] = fv.v;
      } else setPath(s, f, fv.v, fv.remove);
    } else if (el.hasAttribute("data-claim")) {
      var c = sc.claims[Number(el.getAttribute("data-claim"))];
      c[el.getAttribute("data-f")] = fv.v;
    } else return;
    update();
  }
  function renumber() { state.script.shots.forEach(function (s, i) { s.id = i + 1; }); }
  function rebuildForm() {
    $("ug-form").innerHTML = formHtml();
    $("ug-jsonwrap").hidden = !state.json;
    $("ug-fields").hidden = state.json;
    var b = $("ug-jsonbtn");
    b.setAttribute("aria-pressed", state.json ? "true" : "false");
    b.textContent = state.json ? "Back to the form" : "Edit as JSON";
    if (state.json) $("ug-json").value = JSON.stringify(state.script, null, 2);
  }
  function onClick(e) {
    var t = e.target.closest("[data-act],#ug-blank,#ug-jsonbtn,.ug-tab");
    if (!t) return;
    var act = t.getAttribute("data-act"), sc = state.script;
    if (t.classList.contains("ug-tab")) { setModel(t.getAttribute("data-model"), true); return; }
    if (t.id === "ug-blank") { state.slug = ""; state.script = clone(D.blank); rebuildForm(); update(); say("Blank script loaded."); track("tool_used", { action: "load_example", example: "blank", source: "button" }); return; }
    if (t.id === "ug-jsonbtn") { state.json = !state.json; rebuildForm(); update(); return; }
    if (act === "add-shot") {
      var last = sc.shots[sc.shots.length - 1], end = last && typeof last.end_s === "number" ? last.end_s : 0;
      sc.shots.push({ id: sc.shots.length + 1, beat: "demo", start_s: end, end_s: end + 2, visual: "", caption: "", voiceover: "", source: "text-to-video", prompt: {} });
    } else if (act === "rm-shot") { sc.shots.splice(Number(t.getAttribute("data-i")), 1); renumber(); }
    else if (act === "add-claim") { if (!Array.isArray(sc.claims)) sc.claims = []; sc.claims.push({ text: "", evidence: "" }); }
    else if (act === "rm-claim") sc.claims.splice(Number(t.getAttribute("data-i")), 1);
    else return;
    rebuildForm(); update();
  }
  function onKey(e) {
    var t = e.target.closest(".ug-tab");
    if (!t || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
    var i = MODEL_NAMES.indexOf(t.getAttribute("data-model")), n = (i + (e.key === "ArrowRight" ? 1 : MODEL_NAMES.length - 1)) % MODEL_NAMES.length;
    setModel(MODEL_NAMES[n], true);
    var nt = document.querySelector('.ug-tab[data-model="' + MODEL_NAMES[n] + '"]');
    if (nt) nt.focus();
    e.preventDefault();
  }
  function setModel(m, user) {
    if (MODEL_NAMES.indexOf(m) < 0) return;
    state.model = m;
    update();
    if (user) track("tool_used", { action: "switch_tab", model: m });
  }

  function update() {
    var out = $("ug-out");
    if (!out) return;
    var r;
    try { r = lint(state.script); } catch (err) { out.innerHTML = '<p class="ug-sum ug-sum-bad"><b>✕ Cannot check this script</b> <span>' + esc(err && err.message ? err.message : "invalid shape") + "</span></p>"; return; }
    var st = null, panel = "";
    try { st = stats(state.script); panel = panelHtml(state.script, state.model, state.slug); } catch (err2) { panel = ""; }
    $("ug-lint").innerHTML = lintHtml(r);
    $("ug-stats").innerHTML = st ? statsHtml(st) : "";
    $("ug-tabs").innerHTML = tabsHtml(state.model);
    $("ug-panel").innerHTML = panel;
    var key = r.errors.length + "/" + r.warnings.length + "/" + state.slug;
    clearTimeout(renderTimer);
    renderTimer = setTimeout(function () {
      if (key === lastKey) return;
      lastKey = key;
      track("tool_used", { action: "lint_run", errors: r.errors.length, warnings: r.warnings.length, example: state.slug || "custom" });
    }, 2000);
  }

  function exampleScript(slug) {
    if (slug === D.badSlug) return D.bad;
    return D.examples[slug] || null;
  }
  function loadExample(slug, source) {
    var s = exampleScript(slug);
    if (!s) { state.slug = ""; return false; }
    state.slug = slug; state.script = clone(s);
    rebuildForm(); update();
    say("Loaded " + (slug === D.badSlug ? "the failing example" : D.titles[slug]) + ".");
    track("tool_used", { action: "load_example", example: slug, source: source || "select" });
    return true;
  }
  function onJson() {
    var v = $("ug-json").value;
    try {
      var o = JSON.parse(v);
      if (!o || typeof o !== "object" || Array.isArray(o)) throw new Error("the top level must be an object");
      state.script = o; state.slug = "";
      say("JSON parsed.");
      update();
    } catch (err) { say("JSON error: " + err.message + ". The result below is from the last valid version."); }
  }

  function init() {
    var node = $("ug-data"), root = $("ug-form");
    if (!node || !root) return;
    D = JSON.parse(node.textContent.replace(/<\\\//g, "</"));
    state.slug = root.getAttribute("data-example") || D.order[0];
    state.script = clone(exampleScript(state.slug));
    var q = new URLSearchParams(location.search), ex = q.get("example"), m = q.get("model");
    if (m === "veo") m = "veo3";
    if (m && MODEL_NAMES.indexOf(m) >= 0) state.model = m;
    if (ex === "bad" || ex === "fake-testimonial") ex = D.badSlug;
    rebuildForm();
    if (ex && exampleScript(ex)) loadExample(ex, "link"); else update();
    root.addEventListener("input", function (e) { if (e.target.id === "ug-json") { clearTimeout(onJson.t); onJson.t = setTimeout(onJson, 350); } else onInput(e); });
    root.addEventListener("change", function (e) {
      if (e.target.id === "ug-example") { if (e.target.value) loadExample(e.target.value, "select"); else { state.slug = ""; update(); } }
      else if (e.target.id !== "ug-json") onInput(e);
    });
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    document.documentElement.setAttribute("data-checker", "ready");
  }

  var api = {
    setData: function (d) { D = d; }, lint: lint, stats: stats, renderPrompts: renderPrompts, shotPrompts: shotPrompts, lintHtml: lintHtml,
    statsHtml: statsHtml, panelHtml: panelHtml, tabsHtml: tabsHtml, esc: esc, pyFixed: pyFixed, pyRepr: pyRepr, round2: round2
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  }
})();
