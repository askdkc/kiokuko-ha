"""Read-only namespace registration from canonical source or installed resources."""
from pathlib import Path


def skill_root():
    packaged = Path(__file__).parent / 'skills'
    return packaged if packaged.is_dir() else Path(__file__).resolve().parents[2] / 'skills'


def register(ctx):
    for path in sorted(skill_root().glob('*/SKILL.md')):
        ctx.register_skill(path.parent.name, path, description=f'Kiokuko {path.parent.name}; load with skill_view')
