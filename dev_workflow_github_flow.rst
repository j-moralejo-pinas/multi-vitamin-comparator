Development Workflow
====================

Creating a Feature Branch
--------------------------

1. Make sure you're on the main branch and it's up to date:

.. code-block:: bash

    git checkout main
    git pull

2. Create a new feature branch:

.. code-block:: bash

    git checkout -b feature/your-feature-name

Making Changes
--------------

1. Make your changes in the appropriate files
2. Add tests for new functionality
3. Update documentation if needed
4. Run the test suite to ensure everything works

Running During Development
--------------------------

When running code during development, use:

.. code-block:: bash

    PYTHONPATH='/path/to/package-name/src' python your_script.py

Branching Model and Workflow
============================

This project follows a structured GitHub-Flow branching model to maintain code quality and enable collaborative development.

Branch Types
------------

main
~~~~~~~~
- The production-ready branch
- Contains stable, tested code
- Protected branch requiring pull request reviews
- Only accepts merges from ``feature``, ``hotfix``, ``bugfix``, ``fix``,  and ``meta`` branches

feature/\*
~~~~~~~~~~
- Created for new features or enhancements
- Branched from ``main``
- Naming convention: ``feature/feature-name`` or ``feature/issue-number-description``
- Merged back into ``main`` via pull request

bugfix/\* or fix/\*
~~~~~~~~~~~~~~~~~~~
- Created for non-urgent bug fixes
- Branched from ``main``
- Naming convention: ``bugfix/bug-description`` or ``bugfix/issue-number-description``
- Merged back into ``main`` via pull request

hotfix/\*
~~~~~~~~~
- Created for urgent production fixes
- Branched from ``main``
- Naming convention: ``hotfix/critical-issue-description``
- Merged back into ``main`` via pull request

major/\*
~~~~~~~~
- Created for major changes that may introduce breaking changes
- Branched from ``main`` or a ``feature/`` branch that introduced breaking changes
- Naming convention: ``major/feature-description``
- Merged back into ``main`` via pull request

meta/\*
~~~~~~~
- Created for non-code changes (documentation, CI/CD, etc.)
- Branched from ``main``
- Naming convention: ``meta/change-description``
- Merged back into ``main`` via pull request

Merge Workflows
---------------

Branch → Main
~~~~~~~~~~~~~

1. Merge ``main`` into your branch:

.. code-block:: bash

    git checkout your-branch
    git fetch origin
    git rebase origin/main

2. Create a pull request from ``your-branch`` to ``main``
3. Use **merge commit** to keep track of all changes or **squash and merge** for a clean commit history
4. Delete the feature branch after successful merge

Branch Protection Rules
-----------------------

- ``main``: Requires pull request reviews, status checks must pass
- Direct pushes to ``main`` are prohibited
- All branches must be up-to-date before merging

Workflow Examples
-----------------

**Creating a Feature**

.. code-block:: bash

    # Start from dev
    git checkout dev
    git pull origin dev

    # Create feature branch
    git checkout -b feature/your-feature-name

    # Make changes and commit
    git add .
    git commit -m "feat: implement your-feature-name"

    # Push and create PR
    git push origin feature/your-feature-name

**Preparing for Merge**

.. code-block:: bash

    # Before creating PR, rebase on latest dev
    git fetch origin
    git rebase origin/dev

    # Resolve conflicts if any, then force push
    git push --force-with-lease origin feature/your-feature-name

Submitting Changes
==================

Pull Request Process
--------------------

1. Rebase your feature branch on the latest dev branch:

.. code-block:: bash

    # Fetch the latest changes from upstream
    git fetch origin

    # Rebase your feature branch on dev
    git rebase origin/dev

    # If there are conflicts, resolve them and continue
    git add .
    git rebase --continue

2. Ensure your code passes all tests and linting:

.. code-block:: bash

    # Run the full test suite
    pytest

    # Run all pre-commit hooks (formatting, linting, type checking, etc.)
    pre-commit run --all-files

3. Commit your changes with descriptive commit messages (https://www.conventionalcommits.org/en/v1.0.0/):

.. code-block:: bash

    git add .
    git commit -m "feat: add new feature"

4. Push to your fork:

.. code-block:: bash

    git push origin feature/your-feature-name

5. Create a pull request to dev on GitHub with:

- Reference to any related issues
- Screenshots or examples if applicable
- Clear description of changes in the PR body in the following format [#format]_:

.. code-block:: bash

    - Added: New features or modules
    - Changed: Modifications to existing functionality
    - Fixed: Bug fixes

.. [#format] PR body format is important for automatic changelog generation.

Commit Message Format
---------------------

Use conventional commit format:

- ``feat:``: New features
- ``fix:``: Bug fixes
- ``docs:``: Documentation changes
- ``style:``: Code style changes (formatting, etc.)
- ``refactor:``: Code refactoring
- ``test:``: Adding or updating tests
- ``chore:``: Maintenance tasks
