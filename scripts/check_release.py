#!/usr/bin/env python3
"""Run the release gate. Missing dependencies and any skipped test are failures."""
from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))


def main():
    import jsonschema
    import yaml
    import mcp
    import mcp_types.version
    from intralogistics_flow_analyzer.models import ENGINE_VERSION

    for directory in ('src', 'mcp', 'tests', 'scripts'):
        subprocess.run([sys.executable, '-m', 'compileall', '-q', str(ROOT / directory)], check=True)
    for path in ROOT.rglob('*'):
        if any(p in {'dist','build','.git','__pycache__','.venv'} or p.endswith('.egg-info') for p in path.relative_to(ROOT).parts):
            continue
        if path.suffix == '.json':
            json.loads(path.read_text())
        elif path.suffix in ('.yaml','.yml'):
            yaml.safe_load(path.read_text())
    for filename in ('plugin', 'mcp'):
        schema=json.loads((ROOT / 'schemas/vendor' / f'{filename}.schema.json').read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(json.loads((ROOT / f'{filename}.json').read_text()))
    manifests=[json.loads((ROOT / path).read_text()) for path in ('plugin.json','.codex-plugin/plugin.json','.claude-plugin/plugin.json')]
    assert all(d['version']==ENGINE_VERSION for d in manifests), 'Version mismatch'
    assert len(manifests[0]['extensions']['com.openai']['interface']['defaultPrompt']) <= 3
    assert manifests[0]['extensions']['com.openai']['interface']==manifests[1]['interface']
    agent=yaml.safe_load((ROOT/'skills/intralogistics-flow-analyzer/agents/openai.yaml').read_text())
    assert 'starter_prompts' not in agent['interface']
    print('JSON/YAML, portable schemas and manifest consistency: PASS',flush=True)
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful() or result.skipped:
        print(f'Release gate failed: {len(result.skipped)} skipped test(s).',file=sys.stderr)
        return 1
    for command in (
        ['-m','intralogistics_flow_analyzer.cli','run-evals'],
        ['mcp/smoke_test.py'],['scripts/validate_artifacts.py'],
    ):
        subprocess.run([sys.executable,*command],cwd=ROOT,check=True)
    print('Release gate: PASS (zero skips)',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
