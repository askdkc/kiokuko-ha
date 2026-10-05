"""Copy canonical bundled Skills into build output, never into a user profile."""
from pathlib import Path
from shutil import copytree, rmtree
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildSkills(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / 'hermes_kiokuko' / 'skills'
        if target.exists():
            rmtree(target)
        copytree(Path(__file__).parent / 'skills', target)


setup(cmdclass={'build_py': BuildSkills})
