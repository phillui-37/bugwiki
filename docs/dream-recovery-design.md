# Dream recovery workflow design

Preserve the source-independent transcript export and add durable run state that separates exported batch from acknowledged analysis and committed success. The batch freezes transcript paths and hashes; progress records bounded contiguous offsets and explicit source coverage gaps; acknowledgement remains an honest operator/model declaration. Commit validates owned-path hashes and wiki gates, stages exact owned paths with a temporary index, and writes a post-commit receipt. No-fact batches can complete with explicit acknowledgement and verification. No scheduler or live wiki changes.

Approaches considered: bolt progress into the exporter (least code, couples fetching and analysis); create a small CLI helper behind `wiki dream` (chosen, keeps Bash entrypoint and Python workflow state cohesive); introduce a general orchestration framework (unneeded scope). Tests exercise the public CLI in temporary offline wiki roots.
