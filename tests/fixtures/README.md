# Synthetic fixture policy

The test suite generates fixtures in temporary directories so binary build outputs do not accumulate in the repository.

Fixtures deliberately cover:

- a complete valid V2 project with direction-review evidence;
- an atlas with the wrong dimensions;
- visible pixels in an unused cell;
- one-state editing with unchanged-state hash checks;
- interruption and resume after a partial row set;
- installation replacement, backup, and rollback;
- linked variant isolation.

These images are geometric test data only. They are never accepted as production pet artwork or used to bypass `$imagegen`.
