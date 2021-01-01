# Changelog

This be the changelog for Conway's markdown (CMD),
which adhereth unto [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [v2.0.1] (2021-01-01)

### Changed

- Fixed list item processing by requiring attribute specification
  or whitespace after the list item delimiter.
  This ensures that something like
  
  ````cmd
  ====
  * text
    *em*
  ====
  ````
  doesn't get parsed as a 2-item list.

## [v2.0.0] (2020-12-07)

### Added

- Special value `\-` for attribute omission.

### Changed

- For link and image attributes,
  the empty-string escape `\/` now provides an empty attribute
  instead of omitting it.

## [v1.0.0] (2020-09-02)

- Initial release.

[Unreleased]: https://github.com/conway-markdown/conway-markdown/compare/v2.0.1...HEAD
[v2.0.1]: https://github.com/conway-markdown/conway-markdown/compare/v2.0.0...v2.0.1
[v2.0.0]: https://github.com/conway-markdown/conway-markdown/compare/v1.0.0...v2.0.0
[v1.0.0]: https://github.com/conway-markdown/conway-markdown/releases/tag/v1.0.0
