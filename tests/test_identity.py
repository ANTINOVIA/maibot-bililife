from pathlib import Path
import json

from bililife.identity import PLUGIN_ID, PLUGIN_DIRECTORY


def test_new_identity_matches_manifest_and_active_paths():
    source = Path(__file__).resolve().parents[1]
    assert PLUGIN_ID == 'antinova.maibot-bililife'
    assert PLUGIN_ID == PLUGIN_ID.lower()
    assert PLUGIN_DIRECTORY == 'antinova_maibot-bililife'
    assert json.loads((source / '_manifest.json').read_text(encoding='utf-8'))['id'] == PLUGIN_ID
    for relative in ('README.md', 'docs/OPERATIONS.md', 'plugin.py'):
        content = (source / relative).read_text(encoding='utf-8')
        assert 'local.maibot-bililife' not in content
        assert 'ANTINOVA.maibot-bililife' not in content
