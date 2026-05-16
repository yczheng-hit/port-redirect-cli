from setuptools import setup, find_packages

setup(
    name="port-redirect",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "port-redirect=port_redirect.cli:main",
        ],
    },
    python_requires=">=3.8",
    author="port-redirect",
    description="TCP port forwarding CLI tool",
)