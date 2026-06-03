# ABC Chain Example

This example creates a local four-repository FreeCM fixture:

```text
AppA
|-- LibB
|   |-- LibC
|   `-- LibD
`-- LibC
    `-- LibD
```

Create the fixture in a temporary directory:

```bash
python3 examples/abc-chain/create-fixture.py /tmp/freecm-abc-chain --force
```

Run the FreeCM workflow from the generated app repository:

```bash
cd /tmp/freecm-abc-chain/AppA
python3 configs/source_root_workflow.py --init
python3 configs/source_root_workflow.py --update
python3 configs/source_roots.py graph --format dot
```

The generated `configs/source_root_workflow.py` binds the CMake dependency build
order as `LibD`, `LibC`, then `LibB`. With CMake and Ninja available, this
configure command demonstrates the parent-owned build order:

```bash
cmake -S . -B build/abc-chain-demo -G Ninja
```

The dependency repositories are generated under `remotes/`; the app materializes
them under `AppA/build/dependency_source_roots/` after `--update`.
