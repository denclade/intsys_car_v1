# intsys car v1

This repository contains code run on our intsys cars (v1). It's primarily code to control the motors, servos and sensors.

## Build & upload package

```bash
python -m build
python3 -m twine upload --repository testpypi dist/* --verbose
python3 -m twine upload dist/*
```
