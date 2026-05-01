"""
Sync the Anthropic managed agent (CLAUDE_MANAGED_AGENT_ID) with:
  * the latest tool specs from bot/plugins/places.py (description + schema),
  * an extra `## Walking routes` block appended to the server-side system prompt.

Other server-side tools (agent_toolset, wolfram, tts, latex, html2image) are
preserved verbatim — only the four places.py tools are overwritten.

Usage:
  set -a && source .env_claude && set +a
  ./venv/bin/python useful_scripts/update_claude_agent.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bot'))

from constants import MULTIUSER_CHAT_INSTRUCTIONS  # noqa: E402
from plugins.places import PlacesPlugin  # noqa: E402


PLACES_TOOL_NAMES = {
    'search_places',
    'find_nearby_places',
    'present_places',
    'get_directions',
}

def extract_walks_block() -> str:
    marker = 'WALKING ROUTES (when you call get_directions):'
    instructions = MULTIUSER_CHAT_INSTRUCTIONS.strip()
    if marker not in instructions:
        raise RuntimeError(f'Could not find marker in MULTIUSER_CHAT_INSTRUCTIONS: {marker}')
    _, _, tail = instructions.partition(marker)
    return (
        '\n\n## Walking routes (when you call get_directions)\n'
        + tail.rstrip()
        + '\n'
    )


WALKS_BLOCK = extract_walks_block()


def openai_spec_to_anthropic(spec: dict) -> dict:
    """Convert a plugin's OpenAI-style tool spec to an Anthropic agent tool."""
    return {
        'type': 'custom',
        'name': spec['name'],
        'description': spec['description'],
        'input_schema': spec['parameters'],
    }


def normalize_existing_tool(tool) -> dict:
    """Anthropic SDK returns tool objects; convert to plain dict for update()."""
    if hasattr(tool, 'model_dump'):
        return tool.model_dump(exclude_none=True)
    if isinstance(tool, dict):
        return {k: v for k, v in tool.items() if v is not None}
    raise TypeError(f'unexpected tool type: {type(tool)}')


def merge_tools(existing, new_places_tools):
    kept = []
    for t in existing:
        d = normalize_existing_tool(t)
        if d.get('type') == 'custom' and d.get('name') in PLACES_TOOL_NAMES:
            continue
        kept.append(d)
    return kept + new_places_tools


def merge_system(existing_system: str) -> str:
    marker = '## Walking routes (when you call get_directions)'
    if marker in existing_system:
        head, _, _ = existing_system.partition(marker)
        return head.rstrip() + '\n\n' + WALKS_BLOCK.lstrip()
    return existing_system.rstrip() + '\n\n' + WALKS_BLOCK.lstrip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would change without calling the API')
    args = parser.parse_args()

    api_key = os.environ.get('CLAUDE_API_KEY')
    agent_id = os.environ.get('CLAUDE_MANAGED_AGENT_ID')
    if not api_key or not agent_id:
        print('ERROR: CLAUDE_API_KEY and CLAUDE_MANAGED_AGENT_ID must be set '
              '(source your .env_claude first).', file=sys.stderr)
        return 2

    client = anthropic.Anthropic(api_key=api_key)
    agent = client.beta.agents.retrieve(agent_id)

    new_places_tools = [
        openai_spec_to_anthropic(s) for s in PlacesPlugin().get_spec()
    ]
    print(f'New places tools ({len(new_places_tools)}):')
    for t in new_places_tools:
        print(f'  - {t["name"]:22s}  desc={len(t["description"]):4d}  '
              f'props={list(t["input_schema"].get("properties", {}).keys())}')

    merged_tools = merge_tools(agent.tools, new_places_tools)
    print(f'\nMerged tool list ({len(merged_tools)}):')
    for t in merged_tools:
        kind = t.get('type')
        name = t.get('name', f'<{kind}>')
        print(f'  - {kind:30s} {name}')

    new_system = merge_system(agent.system or '')
    print(f'\nSystem prompt: {len(agent.system or "")} -> {len(new_system)} chars '
          f'(+{len(new_system) - len(agent.system or "")})')

    if args.dry_run:
        print('\n--dry-run: not calling API')
        return 0

    print('\nCalling agents.update(...)')
    updated = client.beta.agents.update(
        agent_id=agent_id,
        version=agent.version,
        system=new_system,
        tools=merged_tools,
    )
    print(f'OK. agent.version: {agent.version} -> {updated.version}')
    print(f'   updated_at:    {updated.updated_at}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
