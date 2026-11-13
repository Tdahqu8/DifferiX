# Project Structure

Generated on: 2026-11-13T10:53:31Z

```
├── .env.example
├── .env.rsi
├── .gitignore
├── 0-.agent_bindings.json
├── README.md
├── README.txt
├── agents/
│   ├── editor.py
│   ├── researcher.py
│   └── reviewer.py
├── cd
├── changelog-08-21-26.txt
├── changelog-10-06-26.txt
├── cli/
│   └── model_cli.py
├── components/
│   ├── basic.css
│   ├── cta-btn.html
│   ├── cta-btn.md
│   ├── footer.html
│   ├── footer.md
│   ├── header.html
│   ├── header.md
│   ├── result-box.html
│   ├── result-box.md
│   ├── search.js
│   ├── translate.html
│   └── translate.md
├── config.py
├── conftest.py
├── deduplicate.py
├── differix.egg-info/
│   ├── PKG-INFO
│   ├── SOURCES.txt
│   ├── dependency_links.txt
│   ├── entry_points.txt
│   ├── requires.txt
│   └── top_level.txt
├── lessons/
│   ├── observations/
│   │   ├── The palette extractor derived muted and dim text colors.json
│   │   ├── agent_output_validated_against_plan.json
│   │   ├── css_var_must_resolve_before_extraction.json
│   │   ├── deepseek_json_20261016_015f5c.json
│   │   ├── deepseek_json_20261016_036cac.json
│   │   ├── deepseek_json_20261016_1282d0.json
│   │   ├── deepseek_json_20261016_13a2e2.json
│   │   ├── deepseek_json_20261016_34ce4f.json
│   │   ├── deepseek_json_20261016_40a8c3.json
│   │   ├── deepseek_json_20261016_52d9b2.json
│   │   ├── deepseek_json_20261016_6345e0.json
│   │   ├── deepseek_json_20261016_733e20.json
│   │   ├── deepseek_json_20261016_89fb0b.json
│   │   ├── deepseek_json_20261016_90086c.json
│   │   ├── deepseek_json_20261016_ac10f5.json
│   │   ├── deepseek_json_20261016_b388b5.json
│   │   ├── deepseek_json_20261016_f29d1e.json
│   │   ├── deepseek_json_20261016_fad2bb.json
│   │   ├── drift_pin_after_internal_write.json
│   │   ├── editor_must_see_sibling_files.json
│   │   ├── external_service_requires_health_probe.json
│   │   ├── fallback_bounded_by_match_region.json
│   │   ├── fallback_heuristic_must_be_permissive.json
│   │   ├── id_renumber_requires_ref_update.json
│   │   ├── plan_merge_preserves_completed_steps.json
│   │   ├── prompt_drain_after_interactive_input.json
│   │   ├── reference_resolution_before_write.json
│   │   ├── reviewer_needs_cross_file_context.json
│   │   ├── single_llm_call_line_budget.json
│   │   └── structured_llm_output_needs_headroom.json
│   └── promoted/
│       ├── README.json
│       ├── README.txt
│       ├── lesson_01.json
│       ├── lesson_02.json
│       ├── lesson_04.json
│       ├── lessons_03.json
│       └── update_schema_when_adding_steps.json
├── license.txt
├── main.py
├── memory/
│   ├── __init__.py
│   ├── embeddings/
│   │   ├── __init__.py
│   │   ├── factory.py
│   │   └── providers.py
│   ├── manager.py
│   ├── migrator.py
│   ├── ranker.py
│   ├── schemas.py
│   └── storage.py
├── memory.db
├── memory.db-shm
├── memory.db-wal
├── model_tiers.json
├── models_config.json
├── openrouter_models_cache.json
├── palettes/
│   ├── midnight-amber.css
│   └── thecalcook-com.css
├── pyproject.toml
├── run
├── run_tests.sh
├── skills/
│   ├── csharp_gamedev.md
│   ├── editor_rules.md
│   ├── frontend_design.md
│   ├── python.md
│   ├── researcher_rules.md
│   ├── reviewer_rules.md
│   ├── roblox_luau.md
│   ├── video_scenario.md
│   ├── webdev.md
│   └── webdev_reviewer.md
├── state.json
├── status/
│   ├── last_thoughts.md
│   └── reasoning_log.jsonl
├── sync.sh
├── t.py
├── task
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── my_test.py
│   ├── tbg.py
│   ├── test_isolation.py
│   ├── test_reflection.py
│   ├── test_verification_firewall.py
│   ├── test_visual_capture.py
│   ├── test_visual_diff.py
│   └── tvf.py
├── tools/
│   ├── agents_chat.py
│   ├── asset_extractor.py
│   ├── asset_manager.py
│   ├── atomic_changeset.py
│   ├── backup_manager.py
│   ├── changelog_engine.py
│   ├── coherence_checker.py
│   ├── compiler.py
│   ├── component_normalizer.py
│   ├── context_assembler.py
│   ├── contract_validator.py
│   ├── cost_predictor.py
│   ├── drift_guard.py
│   ├── file_manager.py
│   ├── handshake.py
│   ├── lesson_manager.py
│   ├── lesson_memory_bridge.py
│   ├── llm_client.py
│   ├── llm_selector.py
│   ├── model_ranker.py
│   ├── palette_extractor.py
│   ├── plugin_manager.py
│   ├── project_context_gatherer.py
│   ├── project_scanner.py
│   ├── project_structure.py
│   ├── reasoning_logger.py
│   ├── reflection.py
│   ├── sandbox_utils.py
│   ├── security/
│   │   ├── __init__.py
│   │   ├── behavior_guard.py
│   │   ├── behaviour_guard.py
│   │   └── verification_firewall.py
│   ├── security_filter.py
│   ├── session_adr.py
│   ├── skill_manager.py
│   ├── state_manager.py
│   ├── structure_index.py
│   ├── test_schema_consistency.py
│   ├── verification_firewall.py
│   ├── visual_capture.py
│   ├── visual_diff.py
│   ├── visual_reviewer.py
│   └── web_search.py
├── vegex.egg-info/
│   ├── PKG-INFO
│   ├── SOURCES.txt
│   ├── dependency_links.txt
│   ├── entry_points.txt
│   ├── requires.txt
│   └── top_level.txt
├── webdev_pro/
│   ├── plugin.yaml
│   └── skills/
│       ├── base_rules.md
│       ├── styles/
│       │   ├── style_3d_webgl.md
│       │   ├── style_brutalist.md
│       │   ├── style_clean_saas.md
│       │   ├── style_cyberpunk.md
│       │   ├── style_editorial.md
│       │   └── style_polish.md
│       ├── web_design.md
│       ├── web_geo.md
│       ├── web_performance.md
│       └── web_rules.md
└── webdev_pro.differixplugin
```
