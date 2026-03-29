Installation Guide
==================

This guide provides step-by-step instructions for installing and setting up the multi-vitamin-comparator project. Choose the installation section that best fits your needs.

.. contents:: Table of Contents
    :local:
    :depth: 2

Prerequisites
=============

Before installing the project, ensure you have the following requirements:

* **Python 3.13** (required for this project)
* **Git** for cloning the repository
* **Internet connection** for downloading dependencies

User Installation
=================

This section is for users who want to use the project without modifying the source code.

Quick Start
-----------

1. **Clone the Repository**: Clone the project repository from GitHub

.. code-block::

    git clone https://github.com/j-moralejo-pinas/multi-vitamin-comparator.git
    cd multi-vitamin-comparator

2. **Set Up Virtual Environment (Recommended)**: While not mandatory, using a virtual environment is highly recommended to avoid dependency conflicts

.. code-block::

    # Using conda (recommended)
    conda create -n multi-vitamin-comparator-env python=3.13
    conda activate multi-vitamin-comparator-env

    # OR using venv
    python -m venv venv
    # On Linux/macOS:
    source venv/bin/activate
    # On Windows:
    venv\Scripts\activate

3. **Install the Package**: Install the project and its dependencies

.. code-block::

    pip install -e .

4. **Verify Installation**: Test that the installation was successful

.. code-block::

    python -c "import multi_vitamin_comparator; print('Installation successful!')"

Developer Installation
======================

This section is for developers who want to contribute to the project or modify the source code.

Prerequisites
-------------

- Git or GitHub CLI installed
- NixOS or Nix package manager
- direnv and nix-direnv for environment management

Developer Environment Setup
---------------------------

To set up the development environment, run:

.. code-block:: bash

    git clone https://github.com/j-moralejo-pinas/multi-vitamin-comparator.git && cd multi-vitamin-comparator && chmod +x setup-dev.sh && ./setup-dev.sh

This will:

- Install all runtime dependencies including system and Python packages
- Install development tools (pytest, ruff, pre-commit, etc.)
- Install documentation tools (sphinx, sphinx-autoapi)
- Set up direnv to automatically activate the environment when you enter the project directory
- Set up pre-commit hooks for code quality checks

Troubleshooting
===============

**Common Issues**

**Import Errors**

If you encounter import errors, ensure the ``PYTHONPATH`` is set correctly

.. code-block::

    export PYTHONPATH="${PWD}/src:${PYTHONPATH}"

**Virtual Environment Issues**

If you have issues with virtual environments, try

.. code-block::

    # For conda environments
    conda info --envs  # List all environments
    conda activate multi-vitamin-comparator-dev  # Activate the environment

    # For venv environments
    which python  # Check which Python you're using
    pip list  # Check installed packages

**Getting Help**

* Check the project's GitHub issues: https://github.com/j-moralejo-pinas/multi-vitamin-comparator/issues
* Review the documentation for detailed usage examples
* Ensure all dependencies are correctly installed

See Also
========

- `Contributing <CONTRIBUTING.rst>`_ - How to contribute to the project
