from setuptools import setup, find_packages


setup(
    name="codex-supervisor",
    version="0.2.0",
    description="Always-on local supervisor for coding-agent sessions.",
    packages=find_packages(include=["supervisor*", "mcp_tools*"]),
    python_requires=">=3.10",
    install_requires=[
        "anthropic>=0.40.0",
        "openai>=1.50.0",
        "fastapi>=0.115.0",
        "uvicorn>=0.32.0",
        "watchfiles>=0.24.0",
        "psutil>=6.0.0",
        "httpx>=0.27.0",
        "pyyaml>=6.0",
        "pydantic>=2.9.0",
    ],
    extras_require={
        "agent": ["claude-agent-sdk>=0.1.0"],
        "dev": ["pytest>=8.0", "pytest-asyncio>=0.24"],
    },
)
