========================
multi-vitamin-comparator
========================

.. image:: https://img.shields.io/badge/python-3.13+-blue.svg
    :target: https://www.python.org/downloads/
    :alt: Python Version

.. image:: https://img.shields.io/badge/license-MIT-green.svg
    :alt: License

Project Description
-------------------

Compare multivitamin supplements against a target product. Given a folder of supplement labels (images or text files), it extracts ingredients via the OpenAI API and ranks candidates by how closely they match the target.

Key Features
------------

- **Flexible input**: accepts plain text, markdown, and images (PNG, JPG, WEBP) as supplement label sources.
- **Structured extraction**: uses the OpenAI API to parse ingredient names, chemical forms, quantities, units, and % daily values into a normalised JSON schema.
- **Deterministic normalization**: ingredient names are mapped to a controlled canonical ontology locally; the API is only called for unresolved names.
- **Asymmetric scoring**: ranks candidates using a logarithmic distance metric with separate configurable penalties for missing, underdosed, overdosed, and extra ingredients.
- **High-risk aware**: applies stricter penalties for fat-soluble vitamins (e.g. Vitamin A, Vitamin E) when amounts are unknown or excessive.

Quick Start
-----------

1. Install and set your API key:

.. code-block:: bash

    pip install multi-vitamin-comparator
    export OPENAI_API_KEY=sk-...

2. Place your supplement label files (images or ``.txt``/``.md`` files) into an input folder, one file per product.

3. Extract ingredients from all labels:

.. code-block:: bash

    python -m multi_vitamin_comparator.extract_multivitamin_ingredients \
        ./inputs ./outputs

    This produces one JSON per file and an ``outputs/all_results.json`` aggregate.

4. Do the same for your target product, into a separate folder:

.. code-block:: bash

    python -m multi_vitamin_comparator.extract_multivitamin_ingredients \
        ./target_input ./target_output

5. Rank candidates against the target:

.. code-block:: bash

    python -m multi_vitamin_comparator.compare_supplements \
        ./target_output/<target>.json \
        ./outputs/all_results.json \
        ./outputs/ranked_output.json

    Replace ``<target>.json`` with the JSON file generated for your target product.

📚 Documentation
---------------

- 📦 `Installation Guide <docs/installation.rst>`_ - Setup instructions and requirements
- 🤝 `Contributing Guidelines <CONTRIBUTING.rst>`_ - Development standards and contribution process
- 📄 `License <LICENSE.txt>`_ - License terms and usage rights
- 👥 `Authors <AUTHORS.rst>`_ - Project contributors and maintainers
- 📜 `Changelog <CHANGELOG.rst>`_ - Project history and version changes
- 📜 `Code of Conduct <CODE_OF_CONDUCT.rst>`_ - Guidelines for participation and conduct
