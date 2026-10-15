"""Small runtime IPC views; full offline provenance stays in compiler/cache reports."""


def runtime_rail_report(report):
    if report is None:
        return None
    # Runtime validation binds the native QB, every node and its material/chain.
    # The duplicate candidate graph is used only by offline provenance replay.
    return {key: value for key, value in report.items() if key != 'candidate_provenance'}


def runtime_world_result(result):
    """Call in the compiler child, after full validation and any cache save."""
    rails = result['inputs'].get('rails')
    if rails is None:
        return result
    # Copy only the containing dictionaries: never strip the persisted/offline
    # report, and never copy or rewrite native bytes or the checked node table.
    return dict(result, inputs=dict(result['inputs'], rails=dict(rails,
                report=runtime_rail_report(rails['report']))))
