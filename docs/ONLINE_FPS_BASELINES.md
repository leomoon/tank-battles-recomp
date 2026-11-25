# Paired online gameplay measurements

Use `scripts/measure_gameplay.py online-pair` to measure two already-running peers on one Linux computer. The mode reads timing telemetry and public measurement metadata; it does not read account profiles or launch games. Keep the keyboard available and both windows ungrouped and visible throughout. Verify actual gameplay before starting and after the interval. Capture screenshots and frame-budget traces separately from the clean timing interval.

The JSON specification requires `diagnostics_active: []`, `sdk_verbose: false`, `input_source`, `clock_domain: "same-host-monotonic"`, `map`, `render_scale`, `fxaa`, `network_topology`, `peer_path`, `port_translator`, `display_backend` and two `peers`. Each peer needs a safe alphanumeric `label`, `role` (`host` or `invited`), `executable`, `source_commit`, `timing`, `audio` and `window_size`. Use labels such as A/B rather than account names. The capture copies only these fields and hashes each executable. Do not put credentials in specifications. An example populated specification is retained locally at `analysis/online-fps/step1/A-host-spec.json`.

```sh
python scripts/measure_gameplay.py online-pair \
  --spec analysis/online-fps/step1/A-host-spec.json \
  --output analysis/online-fps/step1/new-A-host-interval \
  --seconds 60 --verified-live-visible-ungrouped
```

The report keeps guest flips, completed game frames and window presentations separate, including per-second counts and p50/p95/p99 intervals. Timing rows needed to reproduce the result are retained beside it. It rejects incomplete coverage, duplicate/nonmonotonic events, clock-origin changes and five-second gaps. A capture is pending review until its ending gameplay and continuous visibility have been checked.

Record visibility observations at least once per second as JSONL objects with `ns` (the host's monotonic nanoseconds) and `peers`. Each peer object contains `title` ending in its label, `visible`, `hidden` and `grouped`. A test-specific recorder is retained at `analysis/online-fps/step1/observe.py`. After reviewing the ending scene, accept the interval with:

```sh
python scripts/measure_gameplay.py online-pair-review \
  --output analysis/online-fps/step1/new-A-host-interval \
  --observations analysis/online-fps/step1/observations.jsonl \
  --verified-live-at-end
```

Review requires observations covering the whole interval, no gaps longer than three seconds, and both expected peers visible, unhidden and ungrouped at every observation. `paired_same_host_eligible` identifies this scoped result. `baseline_eligible` stays false so the existing single-process acceptance/comparison tools cannot treat it as their normal clean baseline. Visibility polling does not prove scene correctness or frame uniqueness; the operator must verify gameplay and review startup logs for renderer/runtime failures.

Collect at least three nonoverlapping warmed-up intervals, then swap host assignments and repeat. Compare roles within each pair and report variation across intervals. Keep settings, map and executable identity matched. Infinite lives/time can prevent a match ending during consecutive intervals, but record that setting and keep it identical in both role assignments.

This capture mode aligns independent process origins using one host's monotonic clock. It rejects a different clock domain. For separate machines, capture and verify each peer's interval locally; align the match phase separately and never directly compare raw monotonic timestamps across hosts. Same-machine peer-port translation is a test limitation and must remain explicit in reports. Reproducing the issue on both operating systems does not require duplicating these Linux FPS measurements on Windows.
