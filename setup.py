import os

from setuptools import find_packages, setup


def version():
    with open(os.path.join(os.path.dirname(__file__), "speech_translate/_version.py"), encoding="utf-8") as f:
        return f.readline().split("=")[1].strip().strip('"').strip("'")


def read_me():
    with open("README.md", "r", encoding="utf-8") as f:
        return f.read()


def install_requires():
    with open("requirements.txt", "r", encoding="utf-8") as f:
        req = f.read().splitlines()
        return req


setup(
    name="speech-translate-rev",
    version=version(),
    description="A modern WebView-based desktop app for realtime speech transcription, translation, and file transcription.",
    long_description=read_me(),
    long_description_content_type="text/markdown",
    python_requires=">=3.10",
    author="silverpoetry",
    maintainer="silverpoetry",
    url="https://github.com/silverpoetry/speech-translate-rev",
    project_urls={
        "Source": "https://github.com/silverpoetry/speech-translate-rev",
        "Issues": "https://github.com/silverpoetry/speech-translate-rev/issues",
        "Original project": "https://github.com/Dadangdut33/Speech-Translate",
    },
    license="MIT",
    packages=find_packages(),
    install_requires=install_requires(),
    entry_points={"console_scripts": [
        "speech-translate=speech_translate.__main__:main",
        "speech-translate-rev=speech_translate.__main__:main",
    ]},
    include_package_data=True,
)
