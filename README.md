# Conway-Markdown (CMD)

Conway-Markdown (CMD) is:

- A replacement-driven markup language inspired by Markdown.
- A demonstration of the folly of throwing regex at a parsing problem.
- The result of someone joking that
  "the filenames would look like Windows executables from the 90s".
- Implemented in [Python 3.{whatever Debian stable is at}][python3].
- Licensed under "MIT No Attribution" (MIT-0), see [LICENSE].

For detailed documentation, see <<https://conway-markdown.github.io/>>.

[python3]: https://packages.debian.org/stable/python3
[LICENSE]: LICENSE


## Usage

Since this is just a shitty single-file script,
it will not be turned into a proper Python package.

### Linux terminals, macOS Terminal, Git BASH for Windows

1. Make an alias for `cmd.py`
   in whatever dotfile you configure your aliases in:

   ```bashrc
   alias cmd='path/to/cmd.py'
   ```

2. Invoke the alias to convert a CMD file to HTML:

   ```bash
   $ cmd [-h] [-v] [-x] [file.cmd ...]

   Convert Conway-Markdown (CMD) to HTML.

   positional arguments:
     file.cmd       Name of CMD file to be converted. Abbreviate as `file` or
                    `file.` for increased productivity. Omit to convert all CMD
                    files under the working directory.

   optional arguments:
     -h, --help     show this help message and exit
     -v, --version  show program's version number and exit
     -x, --verbose  run in verbose mode (prints every replacement applied)
   ```

### Windows Command Prompt

1. Add the folder containing `cmd.py` to the `%PATH%` variable

2. Invoke `cmd.py` to convert a CMD file to HTML:

   ```cmd
   > cmd.py [-h] [-v] [-x] [file.cmd ...]

   Convert Conway-Markdown (CMD) to HTML.

   positional arguments:
     file.cmd       Name of CMD file to be converted. Abbreviate as `file` or
                    `file.` for increased productivity. Omit to convert all CMD
                    files under the working directory.

   optional arguments:
     -h, --help     show this help message and exit
     -v, --version  show program's version number and exit
     -x, --verbose  run in verbose mode (prints every replacement applied)
   ```

**WARNING: on Windows, be careful not to run any `.cmd` files by accident;
they might break your computer. God save!**


## Features

- [Specify element attributes] (e.g. `id` and `class`)
- [Write arbitrary text outside code]
- [Use `<b>`, `<i>`, and `<cite>` elements], not just `<strong>` and `<em>`
- [Use `<div>` elements] without falling back to HTML
- [Define your own syntax] as you go

[Specify element attributes]:
  https://conway-markdown.github.io/#cmd-attribute-specifications
[Write arbitrary text outside code]:
  https://conway-markdown.github.io/#literals
[Use `<b>`, `<i>`, and `<cite>` elements]:
  https://conway-markdown.github.io/#inline-semantics
[Use `<div>` elements]:
  https://conway-markdown.github.io/#divisions
[Define your own syntax]:
  https://conway-markdown.github.io/#replacement-rule-syntax
