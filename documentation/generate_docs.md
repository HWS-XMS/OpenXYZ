# generate api docs using Sphinx

```bash
cd ./documentation/docs
sphinx-quickstart
cd ../../
sphinx-apidoc -o documentation/docs/ .
```
change configuration in `conf.py` to include the following:
```python
import os
import sys

sys.path.insert(0, os.path.abspath('../..'))

extensions = ["sphinx.ext.todo", "sphinx.ext.viewcode", "sphinx.ext.autodoc"]
html_theme = 'sphinx_rtd_theme'
```
then, generate the html files using the following command:
```bash
cd ./documentation/docs
make html
```