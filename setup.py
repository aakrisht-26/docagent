"""
DocAgent setup.py — package metadata and install configuration.

Install in development mode:
    pip install -e .

Note: this packages the library only (agents, core, skills, utils). The
Streamlit UI is not a package and there is no console-script entry point.
Start the app with:

    streamlit run ui/app.py

A `docagent` console script pointing at `ui.app:main` used to be declared here.
It could never work: `ui` is excluded from `packages`, so a non-editable
install produced a command that raised ModuleNotFoundError, and even with `ui`
packaged, `main()` cannot serve anything without the Streamlit runtime — it
would run the script in bare mode and exit.
"""

from setuptools import setup, find_packages

with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

with open("requirements.txt", encoding="utf-8") as f:
    install_requires = [
        line.strip()
        for line in f
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="docagent",
    version="1.0.0",
    author="DocAgent Team",
    description="Offline AI document understanding via modular agents and skills",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=["tests*", "ui*"]),
    python_requires=">=3.10",
    install_requires=install_requires,
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
