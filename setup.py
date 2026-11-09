"""
setup.py — makes rag_pipeline an installable package so tests/imports work cleanly.
Run: pip install -e . (editable install from repo root)
"""
from setuptools import setup, find_packages

setup(
    name="rag_pipeline",
    version="0.1.0",
    python_requires=">=3.9",  # installer ships 3.12
    packages=find_packages(),
    install_requires=[],  # managed via requirements.txt
)
