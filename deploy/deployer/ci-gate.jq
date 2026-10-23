# Required workflow evidence must come from our own release-branch pushes.
# Other authored runs still have to pass; a green PR cannot replace a push run.
$policy[0] as $p
| if (.workflow_runs | type) != "array" or (.total_count | type) != "number" then
    error("Invalid Actions response")
  else . end
| . as $response
| [.workflow_runs[]
    | select(.event != "dynamic")
    | select(.path as $path | $p.post_release_workflows | index($path) | not)] as $runs
| [$runs[]
    | select(.head_sha == $sha and .head_repository.full_name == $repository)
    | select(.event == "push")
    | select(.head_branch as $branch | $p.release_branches | index($branch))
    | .path] as $present
| [
    (if ($response.workflow_runs | length) != $response.total_count
      then "Incomplete Actions response; refusing a partial verdict" else empty end),
    ($p.required_workflows[] | select(. as $path | $present | index($path) | not)
      | "Missing required release push: \(.)"),
    ($runs[] | select(.status != "completed" or .conclusion != "success")
      | "\(.path): \(.status // "missing")/\(.conclusion // "pending")"),
    ($runs[] | select(.head_sha != $sha or .head_repository.full_name != $repository)
      | "Wrong commit or repository: \(.path)")
  ] as $blockers
| {ok: ($blockers | length == 0), blockers: $blockers}
