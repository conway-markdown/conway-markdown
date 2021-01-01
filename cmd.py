#!/usr/bin/env python3

"""
----------------------------------------------------------------
cmd.py
----------------------------------------------------------------

A converter from Conway's markdown (CMD) to HTML,
written for the sole purpose of building his site
<https://yawnoc.github.io/>.

Conversion is done entirely using regular expression replacements
and placeholder dictionaries.
Unlike John Gruber's markdown, I use fence-style constructs
to avoid the need for proper parsing.

You   : Why the hell would you use regex to do this?
Conway: It works, really!
You   : You're crazy.
Conway: Oh shut up, I already know that.

Arguments in the docstrings here are enclosed in angle brackets;
this was the most readable choice given the CMD syntax.
In particular <UPPER CASE> is used for mandatory arguments
and <lower case> is used for optional arguments.

Documentation: <https://conway-markdown.github.io/>
GitHub: <https://github.com/conway-markdown/conway-markdown>

Licensed under "MIT No Attribution" (MIT-0), see LICENSE.

"""


import argparse
import copy
import fnmatch
import functools
import os
import re
import string as strings


################################################################
# String processing
################################################################


REGEX_FLAGS = re.ASCII|re.MULTILINE|re.VERBOSE

ANY_CHARACTER_REGEX = r'[\s\S]'
ANYTHING_MINIMAL_REGEX = r'[\s\S]*?'
NON_EMPTY_MINIMAL_REGEX = r'[\s\S]+?'

NOT_CLOSING_SQUARE_BRACKET_MINIMAL_REGEX = '[^]]*?'
NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX = '[^}]*?'

NOT_QUOTE_MINIMAL_REGEX = '[^"]*?'

HORIZONTAL_WHITESPACE_CHARACTER_REGEX = r'[^\S\n]'
LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX = r'^[^\S\n]*'

NOT_WHITESPACE_MINIMAL_REGEX = r'[\S]*?'

NOT_NEWLINE_CHARACTER_REGEX = r'[^\n]'

ASCII_WHITESPACE = strings.whitespace


def strip_whitespace(string):
  """
  Strip string of leading and trailing ASCII whitespace.
  """
  
  string = string.strip(ASCII_WHITESPACE)
  
  return string


def de_indent(string):
  """
  De-indent string.
  
  Trailing horizontal whitespace on the last line is removed.
  Empty lines do not count towards the longest common indentation.
  
  By contrast, textwrap.dedent will remove horizontal whitespace
  from *any* whitespace-only line, even in the middle of the string,
  which is undesirable.
  """
  
  # Remove trailing horizontal whitespace on the last line
  string = re.sub(
    fr'''
      {HORIZONTAL_WHITESPACE_CHARACTER_REGEX} *  \Z
    ''',
    '',
    string,
    flags=REGEX_FLAGS
  )
  
  # Get list of all indentations, either
  # (1) non-empty leading horizontal whitespace, or
  # (2) the leading empty string on a non-empty line.
  indentation_list = re.findall(
    f'''
      ^  {HORIZONTAL_WHITESPACE_CHARACTER_REGEX} +
        |
      ^  (?=  {NOT_NEWLINE_CHARACTER_REGEX}  )
    ''',
    string,
    flags=REGEX_FLAGS
  )
  
  # Remove longest common indentation
  longest_common_indentation = os.path.commonprefix(indentation_list)
  string = re.sub(
    f'''
      ^  {re.escape(longest_common_indentation)}
    ''',
    '',
    string,
    flags=REGEX_FLAGS
  )
  
  return string


def re_indent(number_of_spaces, string):
  """
  Re-indent string.
  
  The string is first de-indented,
  before being indented by the specified number of spaces.
  Lines which are empty after the de-indentation are not re-indented.
  """
  
  string = de_indent(string)
  
  string = re.sub(
    f'''
      ^  (?=  {NOT_NEWLINE_CHARACTER_REGEX}  )
    ''',
    number_of_spaces * ' ',
    string,
    flags=REGEX_FLAGS
  )
  
  return string


def join_staggered(number_of_spaces, *strings):
  """
  Join strings with staggered indentation.
  
  The strings are
  (1) re-indented with N and N + 2 spaces alternatively,
      where N is the specified number of spaces, and
  (2) joined with a preceding newline.
  """
  
  staggering_number_of_spaces = 2
  joined_string = ''
  
  for index_, string in enumerate(strings):
    joined_string += '\n'
    joined_string += re_indent(
      number_of_spaces + staggering_number_of_spaces * (index_ % 2),
      string
    )
  
  return joined_string


def escape_python_backslash(string):
  r"""
  Escape a Python backslash into a double backslash.
    \ becomes \\
  """
  
  string = re.sub(r'\\', r'\\\\', string)
  
  return string


def escape_html_syntax_characters(string):
  """
  Escape the three HTML syntax characters &, <, >.
    & becomes &amp;
    < becomes &lt;
    > becomes &gt;
  """
  
  string = re.sub('&', '&amp;', string)
  string = re.sub('<', '&lt;', string)
  string = re.sub('>', '&gt;', string)
  
  return string


def escape_html_quotes(string):
  """
  Escape the HTML double quote ".
    " becomes &quot;
  """
  
  string = re.sub('"', '&quot;', string)
  
  return string


def escape_html_attribute_value(placeholder_storage, string):
  """
  Escape the characters &, <, >, " in an attribute value.
  Surrounding whitespace not protected by a placeholder is stripped.
  
  Since the string may contain placeholder strings
  (for instance from CMD literals),
  in which the three HTML syntax characters &, <, >
  are already escaped, but the quote " is not:
  (0) The string is de-indented.
  (1) Leading and trailing whitespace is stripped.
  (2) Line continuations are processed.
  (3) The three HTML syntax characters &, <, > are escaped.
  (4) The placeholder strings are replaced with their markup.
  (5) If the string is empty, an empty string is returned.
  (6) The quote " is escaped.
  (7) The string is stored into a new placeholder.
  
  CMD shall always delimit attribute values by double quotes " ",
  never single quotes ' '.
  Therefore single quotes are not escaped as &apos;
  """
  
  string = de_indent(string)
  string = strip_whitespace(string)
  string = process_line_continuations(string)
  string = escape_html_syntax_characters(string)
  string = placeholder_storage.replace_placeholders_with_markup(string)
  if string == '':
    return string
  string = escape_html_quotes(string)
  
  return placeholder_storage.create_placeholder_store_markup(string)


ATTRIBUTE_SPECIFICATION_CHARACTER_ATTRIBUTE_NAME_DICTIONARY = {
  '#': 'id',
  '.': 'class',
  'l': 'lang',
  'r': 'rowspan',
  'c': 'colspan',
  'w': 'width',
  's': 'style',
}


def parse_attribute_specification(attribute_specification):
  """
  Parse an attribute specification string into an attribute dictionary.
  The attribute specification string is split by whitespace,
  with the following forms recognised:
    #<ID>
    .<CLASS>
    l<LANG>
    r<ROWSPAN>
    c<COLSPAN>
    w<WIDTH>
    s<STYLE>
  An optional equals sign may be used after the leading character.
  Unrecognised forms are ignored.
  If the class attribute is specified more than once,
  the new value is appended to the existing values.
  If a non-class attribute is specified more than once,
  the latest specification shall prevail.
  For <STYLE> containing whitespace, use CMD literals.
  """
  
  attribute_dictionary = {'id': '', 'class': ''}
  
  if attribute_specification is None:
    attribute_specification = ''
  attribute_form_list = attribute_specification.split()
  
  for attribute_form in attribute_form_list:
    
    leading_character = attribute_form[0]
    if (
      leading_character in
        ATTRIBUTE_SPECIFICATION_CHARACTER_ATTRIBUTE_NAME_DICTIONARY
    ):
      attribute_name = (
        ATTRIBUTE_SPECIFICATION_CHARACTER_ATTRIBUTE_NAME_DICTIONARY[
          leading_character
        ]
      )
      attribute_value = attribute_form[1:]
      if attribute_value[:1] == '=':
        attribute_value = attribute_value[1:]
      if attribute_name == 'class':
        attribute_dictionary[attribute_name] += f' {attribute_value}'
      else:
        attribute_dictionary[attribute_name] = attribute_value
  
  return attribute_dictionary


def build_html_attributes(placeholder_storage, attribute_dictionary):
  """
  Build a sequence of HTML attributes each of the form
  <ATTRIBUTE NAME>="<ATTRIBUTE VALUE>" with a leading space
  and with the necessary escaping for <ATTRIBUTE VALUE>
  from an attribute dictionary, with
    KEYS: <ATTRIBUTE NAME>
    VALUES: <ATTRIBUTE VALUE>
  An attribute will be omitted if any of the following hold:
  (1) <ATTRIBUTE VALUE> is None.
  (2) <ATTRIBUTE VALUE> stripped of whitespace is empty.
  (3) <ATTRIBUTE VALUE> is the special value \- denoting omission.
  """
  
  attributes = ''
  
  for attribute_name in attribute_dictionary:
    
    attribute_value = attribute_dictionary[attribute_name]
    
    omit_attribute = (
      attribute_value is None
        or
      strip_whitespace(attribute_value) == ''
        or
      attribute_value == r'\-'
    )
    
    if not omit_attribute:
      attribute_value = escape_html_attribute_value(
        placeholder_storage, attribute_value
      )
      attributes += f' {attribute_name}="{attribute_value}"'
  
  return attributes


################################################################
# Temporary storage
################################################################


def replace_by_ordinary_dictionary(
  dictionary, string, placeholder_storage=None
):
  """
  Apply a dictionary of ordinary replacements to a string.
  """
  
  for pattern in dictionary:
    
    replacement = dictionary[pattern]
    if placeholder_storage is not None:
      replacement = (
        placeholder_storage.create_placeholder_store_markup(replacement)
      )
    
    string = re.sub(
      re.escape(pattern),
      replacement,
      string
    )
  
  return string


def replace_by_regex_dictionary(dictionary, string):
  """
  Apply a dictionary of regex replacements to a string.
  The flags re.ASCII, re.MULTILINE, and re.VERBOSE are enabled.
  """
  
  for pattern in dictionary:
    
    replacement = dictionary[pattern]
    
    string = re.sub(
      pattern,
      replacement,
      string,
      flags=REGEX_FLAGS
    )
  
  return string


def process_match_by_ordinary_dictionary(dictionary, match_object):
  """
  Process a match object using a dictionary of ordinary replacements.
  To be passed in the form
    functools.partial(process_match_by_ordinary_dictionary, dictionary)
  as the replacement-function argument to re.sub,
  so that an ordinary replacement dictionary can be used
  to process a regex match object.
  
  If the entire string for the match object
  is a key (pattern) in the dictionary,
  the corresponding value (replacement) is returned;
  otherwise the string is returned as is.
  """
  
  match_string = match_object.group()
  replacement = dictionary.get(match_string, match_string)
  
  return replacement


class PlaceholderStorage:
  """
  Placeholder storage class for managing temporary replacements.
  
  There are many instances in which
  a portion of the markup should not be altered
  by any replacements in the processing to follow.
  To protect these portions of markup from further alteration,
  they are temporarily replaced by placeholder strings of the form
    <X><N><X>
  where
  (a) <X> is U+E000, the first "Private Use Area" code point,
      referred to as the "placeholder marker", and
  (b) <N> is an integer counter which is incremented.
  Actual occurrences of <X> in the original markup
  are themselves to be replaced with a placeholder;
  this ensures that those actual occurrences
  are not confounded with the placeholder strings <X><N><X>.
  
  Assuming that the user does not supply regex or ordinary replacements
  which alter strings of the form <X><N><X>,
  the placeholder strings will reliably protect portions of markup
  from any further processing.
  
  Each portion of markup which is to be protected
  from alteration by further processing
  is stored in a dictionary of ordinary replacements, with
    KEYS: the placeholder strings (<X><N><X>), and
    VALUES: the respective portions of markup.
  """
  
  PLACEHOLDER_MARKER = '\uE000'
  PLACEHOLDER_STRING_COMPILED_REGEX = re.compile(
    f'''
      {PLACEHOLDER_MARKER}
      [0-9] +
      {PLACEHOLDER_MARKER}
    ''',
    flags=REGEX_FLAGS
  )
  
  def __init__(self):
    """
    Initialise placeholder storage.
    A placeholder is created for the placeholder marker X itself.
    """
    
    self.dictionary = {}
    self.counter = 0
    
    self.PLACEHOLDER_MARKER_PLACEHOLDER_STRING = (
      self.create_placeholder_store_markup(self.PLACEHOLDER_MARKER)
    )
  
  def create_placeholder(self):
    """
    Create a placeholder string for the current counter value.
    """
    
    placeholder_string = (
      f'{self.PLACEHOLDER_MARKER}{self.counter}{self.PLACEHOLDER_MARKER}'
    )
    
    return placeholder_string
  
  def create_placeholder_store_markup(self, markup_portion):
    """
    Create a placeholder string for, and store, a markup portion.
    Then increment the counter.
    
    Existing placeholder strings in the markup portion
    are substituted with their corresponding markup portions
    before being stored in the dictionary.
    This ensures that all markup portions in the dictionary
    are free of placeholder strings.
    """
    
    placeholder_string = self.create_placeholder()
    self.dictionary[placeholder_string] = (
      self.replace_placeholders_with_markup(markup_portion)
    )
    self.counter += 1
    
    return placeholder_string
  
  def replace_marker_with_placeholder(self, string):
    """
    Replace the placeholder marker <X> with its own placeholder string.
    This ensures that actual occurrences of <X> in a string
    are not confounded with the placeholder strings <X><N><X>.
    """
    
    string = re.sub(
      self.PLACEHOLDER_MARKER,
      self.PLACEHOLDER_MARKER_PLACEHOLDER_STRING,
      string
    )
    
    return string
  
  def replace_placeholders_with_markup(self, string):
    """
    Replace all placeholder strings with their markup portions.
    <X><N><X> becomes its markup portion as stored in the dictionary.
    """
    
    string = re.sub(
      self.PLACEHOLDER_STRING_COMPILED_REGEX,
      functools.partial(process_match_by_ordinary_dictionary, self.dictionary),
      string
    )
    
    return string


PROPERTY_NAME_REGEX = '[a-zA-Z0-9-]+'


class PropertyStorage:
  """
  Property definition storage class.
  
  Property definitions are specified in the content of the preamble,
  which is split according to leading occurrences of %<PROPERTY NAME>,
  where <PROPERTY NAME> may only contain letters, digits, and hyphens.
  Property definitions end at the next property definition,
  or at the end of the (preamble) content being split.
  
  Property definitions are stored in a dictionary
  of ordinary replacements, with
    KEYS: %<PROPERTY NAME>
    VALUES: <PROPERTY MARKUP>
  These may be referenced by writing %<PROPERTY NAME>,
  called a property string, anywhere else in the document.
  """
  
  def __init__(self):
    """
    Initialise property storage.
    """
    
    self.dictionary = {}
  
  def store_property_markup(self, property_name, property_markup):
    """
    Store markup for a property.
    """
    
    property_string = f'%{property_name}'
    self.dictionary[property_string] = property_markup
  
  def get_property_markup(self, property_name):
    """
    Get property markup for a property.
    """
    
    property_string = f'%{property_name}'
    property_markup = self.dictionary[property_string]
    property_markup = (
      self.replace_property_strings_with_markup(property_markup)
    )
    
    return property_markup
  
  def read_definitions_store_markup(self, preamble_content):
    """
    Read and store property definitions.
    """
    
    re.sub(
      fr'''
        {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
        %
        (?P<property_name>  {PROPERTY_NAME_REGEX}  )
          (?P<property_markup>  {ANYTHING_MINIMAL_REGEX}  )
        (?=
          {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
          %
          {PROPERTY_NAME_REGEX}
            |
          \Z
        )
      ''',
      self.process_definition_match,
      preamble_content,
      flags=REGEX_FLAGS
    )
  
  def process_definition_match(self, match_object):
    """
    Process a single property-definition match object.
    """
    
    property_name = match_object.group('property_name')
    
    property_markup = match_object.group('property_markup')
    property_markup = de_indent(property_markup)
    property_markup = strip_whitespace(property_markup)
    
    self.store_property_markup(property_name, property_markup)
    
    return ''
  
  def replace_property_strings_with_markup(self, string):
    """
    Replace all property strings with their markup.
    """
    
    string = re.sub(
      f'%{PROPERTY_NAME_REGEX}',
      functools.partial(process_match_by_ordinary_dictionary, self.dictionary),
      string
    )
    
    return string


REPLACEMENT_FLAGS = 'ApbtecrilhswZ'
REPLACEMENT_FLAG_REGEX = f'[{REPLACEMENT_FLAGS}]'
REPLACEMENT_STORAGE_DICTIONARY_INITIAL = {
  flag: {}
    for flag in REPLACEMENT_FLAGS
}


class RegexReplacementStorage:
  """
  Regex replacement definition storage class.
  
  Regex replacement definitions are specified in the form
    <flag>{% <PATTERN> % <REPLACEMENT> %}
  and are stored in a dictionary of dictionaries, with
    KEYS: <flag>
    VALUES: <REGEX REPLACEMENT DICTIONARY>
  and each regex replacement dictionary with
    KEYS: <PATTERN>
    VALUES: <REPLACEMENT>
  """
  
  def __init__(self):
    """
    Initialise regex replacement storage.
    """
    
    self.dictionary = copy.deepcopy(REPLACEMENT_STORAGE_DICTIONARY_INITIAL)
  
  def store_replacement(self, flag, pattern, replacement):
    """
    Store a replacement.
    """
    
    self.dictionary[flag][pattern] = replacement
  
  def replace_patterns(self, flag, string):
    """
    Replace all patterns for a flag with their replacements.
    """
    
    string = replace_by_regex_dictionary(self.dictionary[flag], string)
    
    return string


class OrdinaryReplacementStorage:
  """
  Ordinary replacement definition storage class.
  
  Ordinary replacement definitions are specified in the form
    <flag>{: <PATTERN> : <REPLACEMENT> :}
  and are stored in a dictionary of dictionaries, with
    KEYS: <flag>
    VALUES: <ORDINARY REPLACEMENT DICTIONARY>
  and each ordinary replacement dictionary with
    KEYS: <PATTERN>
    VALUES: <REPLACEMENT>
  """
  
  def __init__(self):
    """
    Initialise ordinary replacement storage.
    """
    
    self.dictionary = copy.deepcopy(REPLACEMENT_STORAGE_DICTIONARY_INITIAL)
  
  def store_replacement(self, flag, pattern, replacement):
    """
    Store a replacement.
    """
    
    self.dictionary[flag][pattern] = replacement
  
  def replace_patterns(self, flag, string):
    """
    Replace all patterns for a flag with their replacements.
    """
    
    string = replace_by_ordinary_dictionary(self.dictionary[flag], string)
    
    return string


class ReferenceStorage:
  """
  Reference-style definition storage class.
  
  Reference-style (image and link) definitions
  are specified in the form
    @[<LABEL>]{<attribute specification>} <address> <title> @
  and are stored in a dictionary, with
    KEYS: <LABEL>
    VALUES: <ATTRIBUTE DICTIONARY>
  where <ATTRIBUTE DICTIONARY> is formed from
  <address>, <title>, and <attribute specification>.
  <LABEL> is case insensitive,
  and is stored canonically in lower case.
  """
  
  def __init__(self):
    """
    Initialise reference-style definition storage.
    """
    
    self.dictionary = {}
  
  def store_definition_attribute_dictionary(self,
    placeholder_storage, label, attribute_specification, address, title
  ):
    """
    Store attribute dictionary for a reference-style definition.
    """
    
    label = label.lower()
    label = strip_whitespace(label)
    
    attribute_dictionary = parse_attribute_specification(
      attribute_specification
    )
    
    attribute_dictionary["address"] = address
    attribute_dictionary["title"] = title
    
    self.dictionary[label] = attribute_dictionary
  
  def get_definition_attribute_dictionary(self, label):
    """
    Get attribute dictionary for a reference-style definition.
    
    If no definition exists for the given label, return None.
    """
    
    label = label.lower()
    label = strip_whitespace(label)
    
    attribute_dictionary = copy.deepcopy(self.dictionary.get(label, None))
    
    return attribute_dictionary


################################################################
# Literals
################################################################


def process_literals(placeholder_storage, markup):
  """
  Process CMD literals.
  
  <flags>~~ <CONTENT> ~~
  
  Produces <CONTENT> literally,
  with HTML syntax-character escaping
  and de-indentation for <CONTENT>.
  Whitespace around <CONTENT> is stripped.
  <flags> may consist of zero or more of the following characters:
    u to leave HTML syntax characters unescaped
    c to process line continuations
    w to process whitespace completely
    a to enable all flags above
  For <CONTENT> containing two or more consecutive tildes,
  use a longer run of tildes in the delimiters,
  e.g. "~~~ ~~ blah ~~ ~~~" for "~~ blah ~~".
  """
  
  markup = re.sub(
    fr'''
      (?P<flags>  [ucwa] *  )
      (?P<tildes>  [~] {{2,}}  )
          (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
      (?P=tildes)
    ''',
    functools.partial(process_literal_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_literal_match(placeholder_storage, match_object):
  """
  Process a single CMD-literal match object.
  """
  
  flags = match_object.group('flags')
  enabled_all_flags = 'a' in flags
  enabled_unescaped_flag = enabled_all_flags or 'u' in flags
  enabled_continuations_flag = enabled_all_flags or 'c' in flags
  enabled_whitespace_flag = enabled_all_flags or 'w' in flags
  
  content = match_object.group('content')
  content = de_indent(content)
  content = strip_whitespace(content)
  
  if not enabled_unescaped_flag:
    content = escape_html_syntax_characters(content)
  
  if enabled_continuations_flag:
    content = process_line_continuations(content)
  
  if enabled_whitespace_flag:
    content = process_whitespace(content)
  
  literal = content
  literal = placeholder_storage.create_placeholder_store_markup(literal)
  
  return literal


################################################################
# Display code
################################################################


def process_display_code(placeholder_storage, markup):
  """
  Process display code.
  
  <flags>``{<attribute specification>}
    <CONTENT>
  ``
  
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  
  Produces
    <pre<ATTRIBUTES>><code><CONTENT></code></pre>
  where <ATTRIBUTES> is the sequence of attributes
  built from <attribute specification>,
  with HTML syntax-character escaping
  and de-indentation for <CONTENT>.
  
  <flags> may consist of zero or more of the following characters:
    u to leave HTML syntax characters unescaped
    c to process line continuations
    w to process whitespace completely
    a to enable all flags above
  
  For <CONTENT> containing two or more consecutive backticks
  which are not protected by CMD literals,
  use a longer run of backticks in the delimiters.
  """
  
  markup = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P<flags>  [ucwa] *  )
      (?P<backticks>  [`] {{2,}}  )
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}} ?
        ) ?
      \n
        (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P=backticks)
    ''',
    functools.partial(process_display_code_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_display_code_match(placeholder_storage, match_object):
  """
  Process a single display-code match object.
  """
  
  flags = match_object.group('flags')
  enabled_all_flags = 'a' in flags
  enabled_unescaped_flag = enabled_all_flags or 'u' in flags
  enabled_continuations_flag = enabled_all_flags or 'c' in flags
  enabled_whitespace_flag = enabled_all_flags or 'w' in flags
  
  attribute_specification = match_object.group('attribute_specification')
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  content = match_object.group('content')
  content = de_indent(content)
  
  if not enabled_unescaped_flag:
    content = escape_html_syntax_characters(content)
  
  if enabled_continuations_flag:
    content = process_line_continuations(content)
  
  if enabled_whitespace_flag:
    content = process_whitespace(content)
  
  display_code = (
    f'<pre{attributes}><code>{content}</code></pre>'
  )
  display_code = (
    placeholder_storage.create_placeholder_store_markup(display_code)
  )
  
  return display_code


################################################################
# Inline code
################################################################


def process_inline_code(placeholder_storage, markup):
  """
  Process inline code.
  
  <flags>` <CONTENT> `
  
  Produces
    <code><CONTENT></code>
  with HTML syntax-character escaping for <CONTENT>.
  Whitespace around <CONTENT> is stripped.
  <flags> may consist of zero or more of the following characters:
    u to leave HTML syntax characters unescaped
    c to process line continuations
    w to process whitespace completely
    a to enable all flags above
  
  For <CONTENT> containing one or more consecutive backticks
  which are not protected by CMD literals,
  use a longer run of backticks in the delimiters.
  """
  
  markup = re.sub(
    f'''
      (?P<flags>  [ucwa] *  )
      (?P<backticks>  [`] +  )
        (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
      (?P=backticks)
    ''',
    functools.partial(process_inline_code_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_inline_code_match(placeholder_storage, match_object):
  """
  Process a single inline-code match object.
  """
  
  flags = match_object.group('flags')
  enabled_all_flags = 'a' in flags
  enabled_unescaped_flag = enabled_all_flags or 'u' in flags
  enabled_continuations_flag = enabled_all_flags or 'c' in flags
  enabled_whitespace_flag = enabled_all_flags or 'w' in flags
  
  content = match_object.group('content')
  content = strip_whitespace(content)
  
  if not enabled_unescaped_flag:
    content = escape_html_syntax_characters(content)
  
  if enabled_continuations_flag:
    content = process_line_continuations(content)
  
  if enabled_whitespace_flag:
    content = process_whitespace(content)
  
  inline_code = f'<code>{content}</code>'
  inline_code = (
    placeholder_storage.create_placeholder_store_markup(inline_code)
  )
  
  return inline_code


################################################################
# Comments
################################################################


def process_comments(markup):
  """
  Process comments.
  
  <# <CONTENT> #>
  
  Removed, along with any preceding horizontal whitespace.
  For <CONTENT> containing one or more consecutive hashes
  followed by a closing angle bracket,
  use a longer run of hashes in the delimiters.
  
  Although comments are weaker than literals and code
  they may still be used to remove them, e.g.
    ~~ A <# B #> ~~ becomes A <# B #> with HTML escaping,
  but
    <# A ~~ B ~~ #> is removed entirely.
  In this sense they are stronger than literals and code.
  Therefore, while the comment syntax is not placeholder-protected,
  it is nevertheless accorded this status.
  """
  
  markup = re.sub(
    f'''
      {HORIZONTAL_WHITESPACE_CHARACTER_REGEX} *
      <
        (?P<hashes>  [#] +  )
          (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
        (?P=hashes)
      >
    ''',
    '',
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


################################################################
# Display maths
################################################################


def process_display_maths(placeholder_storage, markup):
  r"""
  Process display maths.
  
  <flags>$${<attribute specification>}
    <CONTENT>
  $$
  
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  
  Produces
    <div<ATTRIBUTES>><CONTENT></div>
  where <ATTRIBUTES> is the sequence of attributes
  built from <attribute specification> with '.js-maths' prepended,
  with HTML syntax-character escaping and de-indentation for <CONTENT>.
  <flags> may consist of zero or more of the following characters:
    w to process whitespace completely
  
  For <CONTENT> containing two or more consecutive dollar signs
  which are not protected by CMD literals,
  e.g. \text{\$$d$, i.e.~$d$~dollars},
  use a longer run of dollar signs in the delimiters.
  
  This is to be used with some sort of JavaScript code
  which renders equations based on the class "js-maths".
  If MathML support becomes widespread in the future,
  this ought to be replaced with some MathML converter.
  """
  
  markup = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P<flags>  [w] *  )
      (?P<dollar_signs>  [$] {{2,}}  )
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}} ?
        ) ?
      \n
        (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P=dollar_signs)
    ''',
    functools.partial(process_display_maths_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_display_maths_match(placeholder_storage, match_object):
  """
  Process a single display-maths match object.
  """
  
  flags = match_object.group('flags')
  enabled_whitespace_flag = 'w' in flags
  
  attribute_specification = match_object.group('attribute_specification')
  if attribute_specification is None:
    attribute_specification = ''
  attribute_specification = '.js-maths ' + attribute_specification
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  content = match_object.group('content')
  content = de_indent(content)
  content = escape_html_syntax_characters(content)
  
  if enabled_whitespace_flag:
    content = process_whitespace(content)
  
  display_maths = f'<div{attributes}>{content}</div>'
  display_maths = (
    placeholder_storage.create_placeholder_store_markup(display_maths)
  )
  
  return display_maths


################################################################
# Inline maths
################################################################


def process_inline_maths(placeholder_storage, markup):
  r"""
  Process inline maths.
  
  <flags>$ <CONTENT> $
  
  Produces
    <span class="js-maths"><CONTENT></span>
  with HTML syntax-character escaping for <CONTENT>.
  Whitespace around <CONTENT> is stripped.
  <flags> may consist of zero or more of the following characters:
    w to process whitespace completely
  
  For <CONTENT> containing one or more consecutive dollar signs
  which are not protected by CMD literals,
  e.g. \text{$x = \infinity$ is very big},
  use a longer run of dollar signs in the delimiters.
  
  This is to be used with some sort of JavaScript code
  which renders equations based on the class "js-maths".
  If MathML support becomes widespread in the future,
  this ought to be replaced with some MathML converter.
  """
  
  markup = re.sub(
    f'''
      (?P<flags>  [w] *  )
      (?P<dollar_signs> [$] +  )
        (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
      (?P=dollar_signs)
    ''',
    functools.partial(process_inline_maths_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_inline_maths_match(placeholder_storage, match_object):
  """
  Process a single inline-maths match object.
  """
  
  flags = match_object.group('flags')
  enabled_whitespace_flag = 'w' in flags
  
  content = match_object.group('content')
  content = strip_whitespace(content)
  content = escape_html_syntax_characters(content)
  
  if enabled_whitespace_flag:
    content = process_whitespace(content)
  
  inline_maths = f'<span class="js-maths">{content}</span>'
  inline_maths = (
    placeholder_storage.create_placeholder_store_markup(inline_maths)
  )
  
  return inline_maths


################################################################
# Inclusions
################################################################


def process_inclusions(placeholder_storage, cmd_name, markup):
  r"""
  Process inclusions.
  
  {+ <FILE NAME> +}
  
  Includes the content of the file <FILE NAME>.
  For <FILE NAME> containing one or more consecutive plus signs
  followed by a closing curly bracket,
  use a longer run of plus signs in the delimiters.
  
  All of the syntax above (CMD literals through to inline maths)
  is processed.
  Unlike nested \input in LaTeX,
  nested inclusions are not processed.
  """
  
  markup = re.sub(
    fr'''
      \{{
        (?P<plus_signs>  [+] +  )
          (?P<file_name>  {ANYTHING_MINIMAL_REGEX}  )
        (?P=plus_signs)
      \}}
    ''',
    functools.partial(process_inclusion_match, placeholder_storage, cmd_name),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_inclusion_match(placeholder_storage, cmd_name, match_object):
  """
  Process a single inclusion match object.
  """
  
  file_name = match_object.group('file_name')
  file_name = strip_whitespace(file_name)
  
  try:
    with open(file_name, 'r', encoding='utf-8') as file:
      content = file.read()
  except FileNotFoundError as file_not_found_error:
    match_string = match_object.group()
    error_message = join_staggered(2,
      f'Inclusion file `{file_name}` not found:',
        str(file_not_found_error),
      'CMD file:',
        f'{cmd_name}.cmd',
      'Offending match:',
        match_string
    )
    raise FileNotFoundError(error_message) from file_not_found_error
  
  content = placeholder_storage.replace_marker_with_placeholder(content)
  
  content = process_literals(placeholder_storage, content)
  content = process_display_code(placeholder_storage, content)
  content = process_inline_code(placeholder_storage, content)
  content = process_comments(content)
  content = process_display_maths(placeholder_storage, content)
  content = process_inline_maths(placeholder_storage, content)
  
  inclusion = content
  
  return inclusion


################################################################
# Regex replacement definitions
################################################################


def process_regex_replacement_definitions(
  placeholder_storage, regex_replacement_storage, cmd_name, markup
):
  """
  Process regex replacement definitions.
  
  <flag>{% <PATTERN> % <REPLACEMENT> %}
  
  Python regex syntax is used,
  and the flags re.ASCII, re.MULTILINE, and re.VERBOSE are enabled.
  
  Whitespace around <PATTERN> and <REPLACEMENT> is stripped.
  For <PATTERN> or <REPLACEMENT> containing
  one or more consecutive percent signs,
  use a longer run of percent signs in the delimiters.
  For <PATTERN> matching any of the syntax above,
  which should not be processed using that syntax, use CMD literals.
  
  <flag> may consist of zero or one of the following characters,
  and specifies when the regex replacement is to be applied:
    A for immediately after processing regex replacement definitions
    p for just before processing preamble
    b for just before processing blocks
    t for just before processing tables
    e for just before processing escapes
    c for just before processing line continuations
    r for just before processing reference-style definitions
    i for just before processing images
    l for just before processing links
    h for just before processing headings
    s for just before processing inline semantics
    w for just before processing whitespace
    Z for just before replacing placeholder strings
  If <flag> is empty, it defaults to A.
  
  All regex replacement definitions are read and stored
  using the regex replacement definition storage class.
  If the same pattern is specified more than once for a given flag,
  the latest definition shall prevail.
  
  WARNING:
    Malicious or careless user-defined regex replacements
    will break the normal CMD syntax.
    To avoid breaking placeholder storage,
    do not use replacements to alter placeholder strings.
    To avoid breaking properties,
    do not use replacements to alter property strings.
  """
  
  markup = re.sub(
    fr'''
      (?P<flag>  {REPLACEMENT_FLAG_REGEX}  ) ?
      \{{
        (?P<percent_signs>  [%] +  )
          (?P<pattern>  {ANYTHING_MINIMAL_REGEX}  )
        (?P=percent_signs)
          (?P<replacement>  {ANYTHING_MINIMAL_REGEX}  )
        (?P=percent_signs)
      \}}
    ''',
    functools.partial(process_regex_replacement_definition_match,
      placeholder_storage,
      regex_replacement_storage,
      cmd_name
    ),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_regex_replacement_definition_match(
  placeholder_storage, regex_replacement_storage, cmd_name, match_object
):
  """
  Process a single regex-replacement match object.
  """
  
  flag = match_object.group('flag')
  if flag is None:
    flag = 'A'
  
  pattern = match_object.group('pattern')
  pattern = strip_whitespace(pattern)
  pattern = placeholder_storage.replace_placeholders_with_markup(pattern)
  
  replacement = match_object.group('replacement')
  replacement = strip_whitespace(replacement)
  
  try:
    re.sub(pattern, '', '', flags=REGEX_FLAGS)
  except re.error as pattern_error:
    match_string = match_object.group()
    error_message = join_staggered(2,
      f'Regex replacement pattern `{pattern}` invalid:',
        str(pattern_error),
      'CMD file:',
        f'{cmd_name}.cmd',
      'Offending match:',
        match_string
    )
    raise re.error(error_message) from pattern_error
  
  try:
    re.sub(pattern, replacement, '', flags=REGEX_FLAGS)
  except re.error as replacement_error:
    match_string = match_object.group()
    error_message = join_staggered(2,
      f'Regex replacement replacement `{replacement}` invalid:',
        str(replacement_error),
      'CMD file:',
        f'{cmd_name}.cmd',
      'Offending match:',
        match_string
    )
    raise re.error(error_message) from replacement_error
  
  regex_replacement_storage.store_replacement(flag, pattern, replacement)
  
  return ''


################################################################
# Ordinary replacement definitions
################################################################


def process_ordinary_replacement_definitions(
  ordinary_replacement_storage, cmd_name, markup
):
  """
  Process ordinary replacement definitions.
  
  <flag>{: <PATTERN> : <REPLACEMENT> :}
  
  Whitespace around <PATTERN> and <REPLACEMENT> is stripped.
  For <PATTERN> or <REPLACEMENT> containing
  one or more consecutive colons,
  use a longer run of colons in the delimiters.
  
  <flag> may consist of zero or one of the following characters,
  and specifies when the ordinary replacement is to be applied:
    A for immediately after processing ordinary replacement definitions
    p for just before processing preamble
    b for just before processing blocks
    t for just before processing tables
    e for just before processing escapes
    c for just before processing line continuations
    r for just before processing reference-style definitions
    i for just before processing images
    l for just before processing links
    h for just before processing headings
    s for just before processing inline semantics
    w for just before processing whitespace
    Z for just before replacing placeholder strings
  If <flag> is empty, it defaults to A.
  
  All ordinary replacement definitions are read and stored
  using the ordinary replacement definition storage class.
  If the same pattern is specified more than once for a given flag,
  the latest definition shall prevail.
  
  WARNING:
    Malicious or careless user-defined ordinary replacements
    will break the normal CMD syntax.
    To avoid breaking placeholder storage,
    do not use replacements to alter placeholder strings.
    To avoid breaking properties,
    do not use replacements to alter property strings.
  """
  
  markup = re.sub(
    fr'''
      (?P<flag>  {REPLACEMENT_FLAG_REGEX}  ) ?
      \{{
        (?P<colons>  [:] +  )
          (?P<pattern>  {ANYTHING_MINIMAL_REGEX}  )
        (?P=colons)
          (?P<replacement>  {ANYTHING_MINIMAL_REGEX}  )
        (?P=colons)
      \}}
    ''',
    functools.partial(process_ordinary_replacement_definition_match,
      ordinary_replacement_storage, cmd_name
    ),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_ordinary_replacement_definition_match(
  ordinary_replacement_storage, cmd_name, match_object
):
  """
  Process a single ordinary-replacement match object.
  """
  
  flag = match_object.group('flag')
  if flag is None:
    flag = 'A'
  
  pattern = match_object.group('pattern')
  pattern = strip_whitespace(pattern)
  
  replacement = match_object.group('replacement')
  replacement = strip_whitespace(replacement)
  
  try:
    re.sub('', replacement, '')
  except re.error as replacement_error:
    match_string = match_object.group()
    error_message = join_staggered(2,
      f'Ordinary replacement replacement `{replacement}` invalid:',
        str(replacement_error),
      'CMD file:',
        f'{cmd_name}.cmd',
      'Offending match:',
        match_string
    )
    raise re.error(error_message) from replacement_error
  
  ordinary_replacement_storage.store_replacement(flag, pattern, replacement)
  
  return ''


################################################################
# Preamble
################################################################


def process_preamble(placeholder_storage, property_storage, cmd_name, markup):
  """
  Process the preamble.
  
  %%
    <CONTENT>
  %%
  
  Produces the HTML preamble,
  i.e. everything from <!DOCTYPE html> through to <body>.
  
  <CONTENT> is split into property definitions
  according to leading occurrences of %<PROPERTY NAME>,
  where <PROPERTY NAME> may only contain letters, digits, and hyphens.
  Property definitions end at the next property definition,
  or at the end of the (preamble) content being split.
  Each property definition is then stored
  using the property definition storage class
  and may be referenced by writing %<PROPERTY NAME>,
  called a property string, anywhere else in the document.
  
  If the same property is specified more than once,
  the latest definition shall prevail.
  
  For <CONTENT> containing two or more consecutive percent signs
  which are not protected by CMD literals,
  use a longer run of percent signs in the delimiters.
  
  Only the first occurrence in the markup is processed.
  
  The following properties, called original properties,
  are accorded special treatment.
  If omitted from a preamble,
  they take the default values shown beside them:
    %lang en
    %viewport width=device-width, initial-scale=1
    %title Title
    %title-suffix
    %author
    %date-created yyyy-mm-dd
    %date-modified yyyy-mm-dd
    %resources
    %description
    %css
    %onload-js
    %footer-copyright-remark
    %footer-remark
  
  The following properties, called derived properties,
  are computed based on the supplied original properties:
    %html-lang-attribute
    %meta-element-author
    %meta-element-description
    %meta-element-viewport
    %title-element
    %style-element
    %body-onload-attribute
    %year-created
    %year-modified
    %year-modified-next
    %footer-element
    %cmd-name
    %url
    %clean-url
  In particular, the year properties are taken
  from the first 4 characters of the appropriate date properties.
  (NOTE: This will break come Y10K.)
  The property %url is computed from the CMD-name argument.
  It is assumed that the current directory
  is the root directory of the website being built,
  so that %url is the URL of the resulting page relative to root.
  """
  
  markup, preamble_count = re.subn(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P<percent_signs>  [%] {{2,}}  )
      \n
        (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P=percent_signs)
    ''',
    functools.partial(process_preamble_match,
      placeholder_storage, property_storage, cmd_name,
    ),
    markup,
    count=1,
    flags=REGEX_FLAGS
  )
  
  if preamble_count > 0:
    
    markup = f'''\
      <!DOCTYPE html>
      <html%html-lang-attribute>
        <head>
          <meta charset="utf-8">
          %meta-element-author
          %meta-element-description
          %meta-element-viewport
          %resources
          %title-element
          %style-element
        </head>
        <body%body-onload-attribute>
          {markup}
        </body>
      </html>
    '''
    markup = property_storage.replace_property_strings_with_markup(markup)
  
  return markup


DEFAULT_ORIGINAL_PROPERTY_DEFINITIONS = '''
  %lang en
  %viewport width=device-width, initial-scale=1
  %title Title
  %title-suffix
  %author
  %date-created yyyy-mm-dd
  %date-modified yyyy-mm-dd
  %resources
  %description
  %css
  %onload-js
  %footer-copyright-remark
  %footer-remark
'''


def process_head_element_content(placeholder_storage, markup):
  """
  Process content for elements inside <head>.
  
  Processes CMD syntax which does not produce new elements,
  i.e. escapes, line continuations, and whitespace,
  and then protects the content with a placeholder.
  """
  
  markup = process_escapes(placeholder_storage, markup)
  markup = process_line_continuations(markup)
  markup = process_whitespace(markup)
  
  return placeholder_storage.create_placeholder_store_markup(markup)


def process_preamble_match(
  placeholder_storage, property_storage, cmd_name, match_object
):
  """
  Process a single preamble match object.
  
  (1) The default property definitions
      for original properties are prepended as defaults
      (which will be overwritten by the supplied properties).
  (2) The properties are stored.
  (3) The derived properties are computed and stored.
  (4) Finally the preamble is removed.
  """
  
  content = match_object.group('content')
  content = DEFAULT_ORIGINAL_PROPERTY_DEFINITIONS + content
  
  property_storage.read_definitions_store_markup(content)
  
  # Derived property %html-lang-attribute
  lang = property_storage.get_property_markup('lang')
  html_lang_attribute = build_html_attributes(
    placeholder_storage, {'lang': lang}
  )
  property_storage.store_property_markup(
    'html-lang-attribute', html_lang_attribute
  )
  
  # Derived property %meta-element-author
  author = property_storage.get_property_markup('author')
  author = process_head_element_content(placeholder_storage, author)
  author = escape_html_attribute_value(placeholder_storage, author)
  if author == '':
    meta_element_author = ''
  else:
    meta_element_author = f'<meta name="author" content="{author}">'
  property_storage.store_property_markup(
    'meta-element-author', meta_element_author
  )
  
  # Derived property %meta-element-description
  description = property_storage.get_property_markup('description')
  description = process_head_element_content(placeholder_storage, description)
  description = escape_html_attribute_value(placeholder_storage, description)
  if description == '':
    meta_element_description = ''
  else:
    meta_element_description = (
      f'<meta name="description" content="{description}">'
    )
  property_storage.store_property_markup(
    'meta-element-description', meta_element_description
  )
  
  # Derived property %meta-element-viewport
  viewport = property_storage.get_property_markup('viewport')
  viewport = escape_html_attribute_value(placeholder_storage, viewport)
  if viewport == '':
    meta_element_viewport = ''
  else:
    meta_element_viewport = f'<meta name="viewport" content="{viewport}">'
  property_storage.store_property_markup(
    'meta-element-viewport', meta_element_viewport
  )
  
  # Derived property %title-element
  title_suffix = property_storage.get_property_markup('title-suffix')
  title = property_storage.get_property_markup('title')
  title += title_suffix
  title = process_head_element_content(placeholder_storage, title)
  title_element = f'<title>{title}</title>'
  property_storage.store_property_markup('title-element', title_element)
  
  # Derived property %style-element
  css = property_storage.get_property_markup('css')
  if css == '':
    style_element = ''
  else:
    style_element = f'<style>{css}</style>'
  property_storage.store_property_markup(
    'style-element', style_element
  )
  
  # Derived property %body-onload-attribute
  onload_js = property_storage.get_property_markup('onload-js')
  onload_js = escape_html_attribute_value(placeholder_storage, onload_js)
  if onload_js == '':
    body_onload_attribute = ''
  else:
    body_onload_attribute = f' onload="{onload_js}"'
  property_storage.store_property_markup(
    'body-onload-attribute', body_onload_attribute
  )
  
  # Derived property %year-created
  date_created = property_storage.get_property_markup('date-created')
  year_created = date_created[:4]
  property_storage.store_property_markup(
    'year-created', year_created
  )
  
  # Derived property %year-modified
  date_modified = property_storage.get_property_markup('date-modified')
  year_modified = date_modified[:4]
  property_storage.store_property_markup(
    'year-modified', year_modified
  )
  
  # Derived property %year-modified-next
  try:
    year_modified_next = f'{int(year_modified) + 1}'
  except ValueError:
    year_modified_next = '????'
  property_storage.store_property_markup(
    'year-modified-next', year_modified_next
  )
  
  # Derived property %footer-element
  year_range = year_created
  try:
    if int(year_modified) > int(year_created):
      year_range += f'--{year_modified}'
  except ValueError:
    pass
  if author == '':
    author_markup = ''
  else:
    author_markup = f'~{author}'
  footer_copyright_remark = (
    property_storage.get_property_markup('footer-copyright-remark')
  )
  if footer_copyright_remark == '':
    footer_copyright_remark_markup = ''
  else:
    footer_copyright_remark_markup = f', {footer_copyright_remark}'
  footer_remark = property_storage.get_property_markup('footer-remark')
  if footer_remark == '':
    footer_remark_markup = ''
  else:
    footer_remark_markup = fr'''
      \+
      {footer_remark}
    '''
  footer_element = f'''
    <footer>
      ©~{year_range}{author_markup}{footer_copyright_remark_markup}.
      {footer_remark_markup}
    </footer>
  '''
  property_storage.store_property_markup(
    'footer-element', footer_element
  )
  
  # Derived property %cmd-name
  property_storage.store_property_markup(
    'cmd-name', cmd_name
  )
  
  # Derived property %url
  url = f'{cmd_name}.html'
  url = re.sub(r'(?:\A|(?<=/))index[.]html\Z', '', url)
  property_storage.store_property_markup(
    'url', url
  )
  
  # Derived property %clean-url
  clean_url = re.sub(r'[.]html\Z', '', url)
  property_storage.store_property_markup(
    'clean-url', clean_url
  )
  
  return ''


################################################################
# Headings
################################################################


def process_headings(placeholder_storage, markup):
  """
  Process headings.
  
  #{<attribute specification>} <CONTENT> #
  
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  
  Produces <h1<ATTRIBUTES>><CONTENT></h1>,
  where <ATTRIBUTES> is the sequence of attributes
  built from <attribute specification>.
  
  Whitespace around <CONTENT> is stripped.
  For <h2> to <h6>, use 2 to 6 delimiting hashes respectively.
  For <CONTENT> containing the delimiting number of
  or more consecutive hashes, use CMD literals.
  """
  
  markup = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P<hashes>  [#] {{1,6}}  )
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}}
        ) ?
      [\s] +
        (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
      (?P=hashes)
    ''',
    functools.partial(process_heading_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_heading_match(placeholder_storage, match_object):
  """
  Process a single heading match object.
  """
  
  hashes = match_object.group('hashes')
  level = len(hashes)
  tag_name = f'h{level}'
  
  attribute_specification = match_object.group('attribute_specification')
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  content = match_object.group('content')
  content = strip_whitespace(content)
  
  heading = f'<{tag_name}{attributes}>{content}</{tag_name}>'
  
  return heading


################################################################
# Blocks
################################################################


BLOCK_DELIMITER_CHARACTER_TAG_NAME_DICTIONARY = {
  '-': 'p',
  '|': 'div',
  '"': 'blockquote',
  '=': 'ul',
  '+': 'ol',
}
BLOCK_DELIMITER_CHARACTER_REGEX = '[-|"=+]'

LIST_TAG_NAMES = ['ul', 'ol']


def process_blocks(placeholder_storage, markup):
  """
  Process blocks.
  
  <C><C><C><C>{<attribute specification>}
    <CONTENT>
  <C><C><C><C>
  
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  
  The following delimiting characters <C> are used:
    Non-lists
      -  <p>
      |  <div>
      "  <blockquote>
    Lists
      =  <ul>
      +  <ol>
  Produces the block
    <<TAG NAME><ATTRIBUTES>>
    <CONTENT></<TAG NAME>>,
  where <ATTRIBUTES> is the sequence of attributes
  built from <attribute specification>.
  For <CONTENT> containing four or more
  consecutive delimiting characters
  which are not protected by CMD literals,
  use a longer run of delimiting characters in the delimiters.
  
  A recursive call is used to process nested blocks.
  
  For list blocks, <CONTENT> is split into list items <li>
  according to list item processing.
  """
  
  markup = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P<delimiter>
        (?P<delimiter_character>  {BLOCK_DELIMITER_CHARACTER_REGEX}  )
        (?P=delimiter_character) {{3,}}
      )
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}} ?
        ) ?
      \n
        (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P=delimiter)
    ''',
    functools.partial(process_block_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_block_match(placeholder_storage, match_object):
  """
  Process a single block match object.
  """
  
  delimiter_character = match_object.group('delimiter_character')
  tag_name = (
    BLOCK_DELIMITER_CHARACTER_TAG_NAME_DICTIONARY[delimiter_character]
  )
  block_is_list = tag_name in LIST_TAG_NAMES
  
  attribute_specification = match_object.group('attribute_specification')
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  content = match_object.group('content')
  
  # Process nested blocks
  content = process_blocks(placeholder_storage, content)
  
  if block_is_list:
    content = process_list_items(placeholder_storage, content)
  
  block = (
    f'<{tag_name}{attributes}>\n{content}</{tag_name}>'
  )
  
  return block


LIST_ITEM_DELIMITER_REGEX = '([*+-]|[0-9]+[.])'


def process_list_items(placeholder_storage, content):
  """
  Process list items.
  
  Content is split into list items <li>
  according to leading occurrences of <Y>{<attribute specification>},
  with the following delimiters <Y>:
    *
    +
    -
    1. (or any run of digits followed by a full stop)
  List items end at the next list item,
  or at the end of the content being split.
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  """
  
  content = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      {LIST_ITEM_DELIMITER_REGEX}
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}}
        ) ?
      [\s] +
      (?P<list_item_content>  {ANYTHING_MINIMAL_REGEX}  )
      (?=
        {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
        {LIST_ITEM_DELIMITER_REGEX}
        (?:
          \{{
            {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
          \}}
        ) ?
        [\s] +
          |
        \Z
      )
    ''',
    functools.partial(process_list_item_match, placeholder_storage),
    content,
    flags=REGEX_FLAGS
  )
  
  return content


def process_list_item_match(placeholder_storage, match_object):
  """
  Process a single list-item match object.
  """
  
  attribute_specification = match_object.group('attribute_specification')
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  list_item_content = match_object.group('list_item_content')
  list_item_content = strip_whitespace(list_item_content)
  
  list_item = f'''
    <li{attributes}>{list_item_content}
    </li>
  '''
  
  return list_item


################################################################
# Tables
################################################################


TABLE_CELL_DELIMITER_TAG_NAME_DICTIONARY = {
  ';': 'th',
  ',': 'td',
}
TABLE_CELL_DELIMITER_REGEX = '[;,]'


TABLE_ROW_DELIMITER_REGEX = '=='


TABLE_PART_DELIMITER_TAG_NAME_DICTIONARY = {
  '|^': 'thead',
  '|:': 'tbody',
  '|_': 'tfoot',
}
TABLE_PART_DELIMITER_REGEX = '[|][\^:_]'


def process_tables(placeholder_storage, markup):
  """
  Process tables.
  
  ''''{<attribute specification>}
    <CONTENT>
  ''''
  
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  
  Produces the table
    <table<ATTRIBUTES>>
    <CONTENT></table>,
  where <ATTRIBUTES> is the sequence of attributes
  built from <attribute specification>.
  For <CONTENT> containing four or more consecutive apostrophes
  which are not protected by CMD literals,
  use a longer run of apostrophes in the delimiters.
  
  A recursive call is used to process nested tables.
  
  <CONTENT> is
  (1) split into table cells <th>, <td>
      according to table cell processing,
  (2) split into table rows <tr>
      according to table row processing, and
  (3) split into table parts <thead>, <tbody>, <tfoot>
      according to table part processing.
  """
  
  markup = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P<apostrophes>  ['] {{4,}}  )
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}} ?
        ) ?
      \n
        (?P<content>  {ANYTHING_MINIMAL_REGEX}  )
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P=apostrophes)
    ''',
    functools.partial(process_table_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_table_match(placeholder_storage, match_object):
  """
  Process a single table match object.
  """
  
  attribute_specification = match_object.group('attribute_specification')
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  content = match_object.group('content')
  
  # Process nested tables
  content = process_tables(placeholder_storage, content)
  
  content = process_table_cells(placeholder_storage, content)
  content = process_table_rows(placeholder_storage, content)
  content = process_table_parts(placeholder_storage, content)
  
  table = f'<table{attributes}>\n{content}</table>'
  
  return table


def process_table_cells(placeholder_storage, content):
  """
  Process table cells.
  
  Content is split into table cells <th>, <td> according to
  leading occurrences of <Z>{<attribute specification>},
  with the following delimiters <Z>:
    ;  <th>
    ,  <td>
  Table cells end at the next table cell, table row, or table part,
  or at the end of the content being split.
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  """
  
  content = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P<delimiter>  {TABLE_CELL_DELIMITER_REGEX}  )
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}}
        ) ?
      (?:
        {HORIZONTAL_WHITESPACE_CHARACTER_REGEX} +
          |
        \n
      )
      (?P<table_cell_content>  {ANYTHING_MINIMAL_REGEX}  )
      (?=
        {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
        (?:
          {TABLE_CELL_DELIMITER_REGEX}
            |
          {TABLE_ROW_DELIMITER_REGEX}
            |
          {TABLE_PART_DELIMITER_REGEX}
        )
          |
        \Z
      )
    ''',
    functools.partial(process_table_cell_match, placeholder_storage),
    content,
    flags=REGEX_FLAGS
  )
  
  return content


def process_table_cell_match(placeholder_storage, match_object):
  """
  Process a single table-cell match object.
  """
  
  delimiter = match_object.group('delimiter')
  tag_name = TABLE_CELL_DELIMITER_TAG_NAME_DICTIONARY[delimiter]
  
  attribute_specification = match_object.group('attribute_specification')
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  table_cell_content = match_object.group('table_cell_content')
  table_cell_content = strip_whitespace(table_cell_content)
  
  table_cell = f'<{tag_name}{attributes}>{table_cell_content}</{tag_name}>\n'
  
  return table_cell


def process_table_rows(placeholder_storage, content):
  """
  Process table rows.
  
  Content is split into table rows <tr>
  according to leading occurrences of =={<attribute specification>}.
  Table rows end at the next table row or table part,
  or at the end of the content being split.
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  """
  
  content = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      {TABLE_ROW_DELIMITER_REGEX}
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}}
        ) ?
      (?:
        {HORIZONTAL_WHITESPACE_CHARACTER_REGEX} +
          |
        \n
      )
      (?P<table_row_content>  {ANYTHING_MINIMAL_REGEX}  )
      (?=
        {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
        (?:
          {TABLE_ROW_DELIMITER_REGEX}
            |
          {TABLE_PART_DELIMITER_REGEX}
        )
          |
        \Z
      )
    ''',
    functools.partial(process_table_row_match, placeholder_storage),
    content,
    flags=REGEX_FLAGS
  )
  
  return content


def process_table_row_match(placeholder_storage, match_object):
  """
  Process a single table-row match object.
  """
  
  attribute_specification = match_object.group('attribute_specification')
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  table_row_content = match_object.group('table_row_content')
  table_row_content = strip_whitespace(table_row_content)
  
  table_row = f'''
    <tr{attributes}>
      {table_row_content}
    </tr>
  '''
  
  return table_row


def process_table_parts(placeholder_storage, content):
  """
  Process table parts.
  
  Content is split into table parts <thead>, <tbody>, <tfoot>
  according to leading occurrences of <Y>{<attribute specification>},
  with the following delimiters <Y>:
    |^  <thead>
    |~  <tbody>
    |_  <tfoot>
  Table parts end at the next table part,
  or at the end of the content being split.
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  """
  
  content = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P<delimiter>  {TABLE_PART_DELIMITER_REGEX}  )
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}}
        ) ?
      (?:
        {HORIZONTAL_WHITESPACE_CHARACTER_REGEX} +
          |
        \n
      )
      (?P<table_part_content>  {ANYTHING_MINIMAL_REGEX}  )
      (?=
        {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
        {TABLE_PART_DELIMITER_REGEX}
          |
        \Z
      )
    ''',
    functools.partial(process_table_part_match, placeholder_storage),
    content,
    flags=REGEX_FLAGS
  )
  
  return content


def process_table_part_match(placeholder_storage, match_object):
  """
  Process a single table-part match object.
  """
  
  delimiter = match_object.group('delimiter')
  tag_name = TABLE_PART_DELIMITER_TAG_NAME_DICTIONARY[delimiter]
  
  attribute_specification = match_object.group('attribute_specification')
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  table_part_content = match_object.group('table_part_content')
  table_part_content = strip_whitespace(table_part_content)
  
  table_part = f'''
    <{tag_name}{attributes}>
      {table_part_content}
    </{tag_name}>
  '''
  
  return table_part


################################################################
# Escapes
################################################################


ESCAPE_REPLACEMENT_DICTIONARY = {
  r'\\': '\\',
  r'\/': '',
  r'\ /': ' ',
  r'\ ': ' ',
  r'\~': '~',
  '~': '&nbsp;',
  r'\0': '&numsp;',
  r'\,': '&thinsp;',
  r'\&': escape_html_syntax_characters('&'),
  r'\<': escape_html_syntax_characters('<'),
  r'\>': escape_html_syntax_characters('>'),
  r'\"': escape_html_quotes('"'),
  '...': '…',
  '---': '—',
  '--': '–',
  r'\P': '¶',
  r'\d': '$',
  r'\#': '#',
  r'\[': '[',
  r'\]': ']',
  r'\(': '(',
  r'\)': ')',
  r'\*': '*',
  r'\_': '_',
  r'\|': '|',
  r'\=': '<hr>',
}


def process_escapes(placeholder_storage, markup):
  r"""
  Process escapes.
    \\  becomes \
    \/  becomes the empty string
    \ / becomes   U+0020 SPACE
    \   becomes   U+0020 SPACE
    \~  becomes ~
    ~   becomes &nbsp;
    \0  becomes &numsp;
    \,  becomes &thinsp;
    \&  becomes &amp;
    \<  becomes &lt;
    \>  becomes &gt;
    \"  becomes &quot;
    ... becomes … U+2026 HORIZONTAL ELLIPSIS
    --- becomes — U+2014 EM DASH
    --  becomes – U+2013 EN DASH
    \P  becomes ¶ U+00B6 PILCROW SIGN
    \d  becomes $
    \#  becomes #
    \[  becomes [
    \]  becomes ]
    \(  becomes (
    \)  becomes )
    \*  becomes *
    \_  becomes _
    \|  becomes |
    \=  becomes <hr>
    \+  becomes <br>
  
  Some of the resulting strings (*, _) must be protected
  from further replacements using placeholder storage,
  but <br> resulting from \+ must not be protected so
  since whitespace before it will be removed later;
  for the remaining strings it does not matter.
  For simplicity in the implementation,
  <br> alone is left unprotected
  whilst everything else is protected.
  """
  
  markup = replace_by_ordinary_dictionary(
    ESCAPE_REPLACEMENT_DICTIONARY, markup, placeholder_storage
  )
  
  markup = re.sub(r'\\\+', '<br>', markup)
  
  return markup


################################################################
# Line continuations
################################################################


def process_line_continuations(markup):
  """
  Process backslash line continuations.
  """
  
  markup = re.sub(
    fr'''
      \\
      \n
      {HORIZONTAL_WHITESPACE_CHARACTER_REGEX} *
    ''',
    '',
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


################################################################
# Reference-style definitions
################################################################


def process_reference_definitions(
  placeholder_storage, reference_storage, markup
):
  """
  Process reference-style definitions.
  
  @[<LABEL>]{<attribute specification>} <address> <title> @
  
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  
  The referencing <LABEL> is case insensitive.
  For definitions whose <address> or <title>
  contains one or more consecutive at signs
  which are not protected by CMD literals,
  use a longer run of at signs in the delimiters.
  
  <address> is used for <src> in images and <href> in links.
  
  All reference-style definitions are read and stored
  using the reference-style definition storage class.
  If the same label (which is case insensitive)
  is specified more than once,
  the latest definition shall prevail.
  """
  
  markup = re.sub(
    fr'''
      {LEADING_HORIZONTAL_WHITESPACE_MAXIMAL_REGEX}
      (?P<at_signs>  [@] +  )
      \[
        (?P<label>  {NOT_CLOSING_SQUARE_BRACKET_MINIMAL_REGEX}  )
      \]
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}}
        ) ?
      (?:
        [\s] *
        (?P<address>  {ANYTHING_MINIMAL_REGEX}  )
        (?:
          [\s] +?
          (?P<title>  {ANYTHING_MINIMAL_REGEX}  )
        ) ??
      ) ??
      (?P=at_signs)
    ''',
    functools.partial(process_reference_definition_match,
      placeholder_storage,
      reference_storage
    ),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_reference_definition_match(
  placeholder_storage, reference_storage, match_object
):
  """
  Process a single reference-style definition match object.
  """
  
  label = match_object.group('label')
  attribute_specification = match_object.group('attribute_specification')
  address = match_object.group('address')
  title = match_object.group('title')
  
  reference_storage.store_definition_attribute_dictionary(
    placeholder_storage, label, attribute_specification, address, title
  )
  
  return ''


################################################################
# Images
################################################################


def process_images(placeholder_storage, reference_storage, markup):
  """
  Process images.
  
  ## Inline-style ##
  
  ![<ALT>](<src> <title>)
  
  Unlike John Gruber's markdown, <title> is not surrounded by quotes.
  If quotes are supplied to <title>,
  they are automatically escaped as &quot;.
  
  Produces the image <img<ATTRIBUTES>>,
  where <ATTRIBUTES> is the sequence of attributes
  built from <ALT>, <src>, and <title>.
  
  For <ALT>, <src>, or <title> containing
  one or more closing square or round brackets, use CMD literals.
  
  ## Reference-style ##
  
  ![<ALT>][<label>]
  
  A single space may be included between [<ALT>] and [<label>].
  The referencing <label> is case insensitive
  (this is handled by the reference-style definition storage class).
  
  If <label> is empty,
  the square brackets surrounding it may be omitted,
  and <ALT> is used as the label.
  
  Produces the image <img<ATTRIBUTES>>,
  where <ATTRIBUTES> is the sequence of attributes
  built from <ALT> and the attribute specifications
  for the corresponding reference-style image definition.
  
  Whitespace around <label> is stripped.
  For images whose <ALT> or <label> contains
  one or more closing square brackets, use CMD literals.
  """
  
  # Inline-style
  markup = re.sub(
    fr'''
      !
      \[
        (?P<alt>  {NOT_CLOSING_SQUARE_BRACKET_MINIMAL_REGEX}  )
      \]
      \(
        [\s] *
        (?P<src>  {ANYTHING_MINIMAL_REGEX}  )
        (?:
          [\s] +?
          (?P<title>  {ANYTHING_MINIMAL_REGEX}  )
        ) ??
      \)
    ''',
    functools.partial(process_inline_image_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  # Reference-style
  markup = re.sub(
    fr'''
      !
      \[
        (?P<alt>  {NOT_CLOSING_SQUARE_BRACKET_MINIMAL_REGEX}  )
      \]
      (?:
        [ ] ?
        \[
          (?P<label>  {NOT_CLOSING_SQUARE_BRACKET_MINIMAL_REGEX}  )
        \]
      ) ?
    ''',
    functools.partial(process_reference_image_match,
      placeholder_storage,
      reference_storage
    ),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_inline_image_match(placeholder_storage, match_object):
  """
  Process a single inline-style-image match object.
  """
  
  alt = match_object.group('alt')
  src = match_object.group('src')
  title = match_object.group('title')
  
  attribute_dictionary = {
    'alt': alt,
    'src': src,
    'title': title,
  }
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  image = f'<img{attributes}>'
  
  return image


def process_reference_image_match(
  placeholder_storage, reference_storage, match_object
):
  """
  Process a single reference-style-image match object.
  
  If no image is defined for the given label,
  return the entire string for the matched object as is.
  
  <address> specified in the definition is used for <src>.
  <title> is moved to last in the sequence of attributes.
  """
  
  alt = match_object.group('alt')
  
  label = match_object.group('label')
  if label is None or strip_whitespace(label) == '':
    label = alt
  
  definition_attribute_dictionary = (
    reference_storage.get_definition_attribute_dictionary(label)
  )
  if definition_attribute_dictionary is None:
    match_string = match_object.group()
    return match_string
  
  match_attribute_dictionary = {
    'alt': alt,
  }
  
  attribute_dictionary = {
    **match_attribute_dictionary,
    **definition_attribute_dictionary,
  }
  
  attribute_dictionary['src'] = attribute_dictionary.pop('address')
  attribute_dictionary['title'] = attribute_dictionary.pop('title')
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  image = f'<img{attributes}>'
  
  return image


################################################################
# Links
################################################################


def process_links(placeholder_storage, reference_storage, markup):
  """
  Process links.
  
  ## Inline-style ##
  
  [<CONTENT>](<href> <title>)
  
  Unlike John Gruber's markdown, <title> is not surrounded by quotes.
  If quotes are supplied to <title>,
  they are automatically escaped as &quot;.
  
  Produces the link <a<ATTRIBUTES>><CONTENT></a>,
  where <ATTRIBUTES> is the sequence of attributes
  built from <href> and <title>.
  
  Whitespace around <CONTENT> is stripped.
  For <CONTENT>, <href>, or <title> containing
  one or more closing square or round brackets, use CMD literals.
  
  ## Reference-style ##
  
  [<CONTENT>][<label>]
  
  A single space may be included between [<CONTENT>] and [<label>].
  The referencing <label> is case insensitive
  (this is handled by the reference-style definition storage class).
  
  If <label> is empty,
  the square brackets surrounding it may be omitted,
  and <CONTENT> is used as the label.
  
  Produces the link <a<ATTRIBUTES>><CONTENT></a>,
  where <ATTRIBUTES> is the sequence of attributes
  built from the attribute specifications
  for the corresponding reference-style link definition.
  
  Whitespace around <CONTENT> and <label> is stripped.
  For links whose <CONTENT> or <label> contains
  one or more closing square brackets, use CMD literals.
  """
  
  # Inline-style
  markup = re.sub(
    fr'''
      \[
        (?P<content>  {NOT_CLOSING_SQUARE_BRACKET_MINIMAL_REGEX}  )
      \]
      \(
        [\s] *
        (?P<href>  {ANYTHING_MINIMAL_REGEX}  )
        (?:
          [\s] +?
          (?P<title>  {ANYTHING_MINIMAL_REGEX}  )
        ) ??
      \)
    ''',
    functools.partial(process_inline_link_match, placeholder_storage),
    markup,
    flags=REGEX_FLAGS
  )
  
  # Reference-style links
  markup = re.sub(
    fr'''
      \[
        (?P<content>  {NOT_CLOSING_SQUARE_BRACKET_MINIMAL_REGEX}  )
      \]
      (?:
        [ ] ?
        \[
          (?P<label>  {NOT_CLOSING_SQUARE_BRACKET_MINIMAL_REGEX}  )
        \]
      ) ?
    ''',
    functools.partial(process_reference_link_match,
      placeholder_storage,
      reference_storage
    ),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_inline_link_match(placeholder_storage, match_object):
  """
  Process a single inline-style-link match object.
  """
  
  content = match_object.group('content')
  content = strip_whitespace(content)
  
  href = match_object.group('href')
  title = match_object.group('title')
  
  attribute_dictionary = {
    'href': href,
    'title': title,
  }
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  link = f'<a{attributes}>{content}</a>'
  
  return link


def process_reference_link_match(
  placeholder_storage, reference_storage, match_object
):
  """
  Process a single reference-style-link match object.
  
  If no link is defined for the given label,
  return the entire string for the matched object as is.
  
  <address> specified in the definition is used for <href>.
  <title> is moved to last in the sequence of attributes.
  """
  
  content = match_object.group('content')
  content = strip_whitespace(content)
  
  label = match_object.group('label')
  if label is None or strip_whitespace(label) == '':
    label = content
  
  definition_attribute_dictionary = (
    reference_storage.get_definition_attribute_dictionary(label)
  )
  if definition_attribute_dictionary is None:
    match_string = match_object.group()
    return match_string
  
  attribute_dictionary = definition_attribute_dictionary
  
  attribute_dictionary['href'] = attribute_dictionary.pop('address')
  attribute_dictionary['title'] = attribute_dictionary.pop('title')
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  link = f'<a{attributes}>{content}</a>'
  
  return link


################################################################
# Inline semantics
################################################################


INLINE_SEMANTIC_DELIMITER_TAG_NAME_DICTIONARY = {
  '*': 'em',
  '**': 'strong',
  '_' : 'i',
  '__': 'b',
  '"""': 'q',
  "'''": 'cite',
}


def process_inline_semantics(placeholder_storage, markup):
  r"""
  Process inline semantics.
  
  <optional pipe><X>{<attribute specification>} <CONTENT><X>
  
  <CONTENT> must be non-empty.
  The opening delimiter <X> must not be followed by whitespace
  or by </ (which would presumably be a closing tag).
  The closing delimiter <X> must not be preceded by whitespace
  or by a pipe |.
  If <attribute specification> is empty,
  the curly brackets surrounding it may be omitted.
  
  The following delimiters <X> are used:
    *   <em>
    **  <strong>
    _   <i>
    __  <b>
    ""\" <q>
    ''' <cite>
  
  Produces the inline semantic
  <<TAG NAME><ATTRIBUTES>><CONTENT></<TAG NAME>>,
  where <ATTRIBUTES> is the sequence of attributes
  built from <attribute specification>.
  
  The leading <optional pipe> is to be used
  as a disambiguator in some edge cases.
  If present, it indicates that the delimiter directly after it
  is opening rather than closing.
  
  Whitespace around <CONTENT> is stripped.
  For <CONTENT> containing one or more occurrences of * or _,
  or three or more consecutive occurrences of " or ',
  use CMD literals or \* and \_.
  For <CONTENT> ending in a pipe, use the escape \|.
  
  Multiple passes are used to process nested inline semantics
  with the same delimiting character
  (with <CONTENT> only matched during each pass
  if it does not contain the delimiting character).
  Delimiter matching is inner-greedy,
  so ***blah*** produces
    <em><strong>blah</strong></em>
  rather than
    <strong><em>blah</em></strong>.
  For the latter, use the optional pipe for the inner occurrence,
  i.e. **|*blah***.
  
  Recursive calls are used to process nested inline semantics
  with a different delimiting character.
  """
  
  markup_has_changed = True
  
  while markup_has_changed:
    
    new_markup = process_inline_semantics_single_pass(
      placeholder_storage, markup
    )
    
    if new_markup == markup:
      markup_has_changed = False
    
    markup = new_markup
  
  return markup


def process_inline_semantics_single_pass(placeholder_storage, markup):
  """
  Process inline semantics single pass.
  """
  
  markup = re.sub(
    fr'''
      [|] ?
      (?P<delimiter>
        (?P<delimiter_character>
          (?P<single_or_double>  [*_]  )
            |
          (?P<triple_only>  ["']  )
        )
        (?(single_or_double)
          (?P=single_or_double) ?
            |
          (?P=triple_only) {{2}}
        )
      )
      (?! [\s] | [<][/] )
        (?:
          \{{
            (?P<attribute_specification>
              {NOT_CLOSING_CURLY_BRACKET_MINIMAL_REGEX}
            )
          \}}
        ) ?
        (?P<content>
          (?:
            (?!  (?P=delimiter_character)  )
            {ANY_CHARACTER_REGEX}
          ) +?
        )
      (?<! [\s] | [|] )
      (?P=delimiter)
    ''',
    functools.partial(process_inline_semantic_match,
      placeholder_storage
    ),
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


def process_inline_semantic_match(placeholder_storage, match_object):
  """
  Process a single inline-semantic match object.
  """
  
  delimiter = match_object.group('delimiter')
  tag_name = INLINE_SEMANTIC_DELIMITER_TAG_NAME_DICTIONARY[delimiter]
  
  attribute_specification = match_object.group('attribute_specification')
  attribute_dictionary = parse_attribute_specification(attribute_specification)
  attributes = build_html_attributes(placeholder_storage, attribute_dictionary)
  
  content = match_object.group('content')
  content = strip_whitespace(content)
  
  # Process nested inline semantics
  content = process_inline_semantics(placeholder_storage, content)
  
  inline_semantic = f'<{tag_name}{attributes}>{content}</{tag_name}>'
  
  return inline_semantic


################################################################
# Whitespace
################################################################


def process_whitespace(markup):
  """
  Process whitespace.
  
  (1) Leading and trailing horizontal whitespace is removed.
  (2) Empty lines are removed.
      (In the implementation, consecutive newlines
      are normalised to a single newline.)
  (3) Whitespace before line break elements <br> is removed.
  (4) Whitespace for attributes is canonicalised:
      (a) a single space is used before the attribute name, and
      (b) no whitespace is used around the equals sign.
  """
  
  markup = re.sub(
    f'''
      ^  {HORIZONTAL_WHITESPACE_CHARACTER_REGEX} +
        |
      {HORIZONTAL_WHITESPACE_CHARACTER_REGEX} +  $
    ''',
    '',
    markup,
    flags=REGEX_FLAGS
  )
  markup = re.sub(r'[\n]+', r'\n', markup)
  markup = re.sub(r'[\s]+(?=<br>)', '', markup, flags=REGEX_FLAGS)
  markup = re.sub(
    fr'''
      [\s] +?
        (?P<attribute_name>  [\S] +?  )
      [\s] *?
        =
      [\s] *?
        (?P<quoted_attribute_value>  "  {NOT_QUOTE_MINIMAL_REGEX}  ")
    ''',
    r' \g<attribute_name>=\g<quoted_attribute_value>',
    markup,
    flags=REGEX_FLAGS
  )
  
  return markup


################################################################
# Converter
################################################################


def cmd_to_html(cmd, cmd_name):
  """
  Convert CMD to HTML.
  
  The CMD-name argument
  (1) determines the URL of the resulting page,
      which is stored in the property %url, and
  (2) is used for the name of the CMD file in error messages.
  It is assumed that the current directory
  is the root directory of the website being built,
  so that %url is the URL of the resulting page relative to root.
  
  During the conversion, the string is neither CMD nor HTML,
  and is referred to as "markup".
  """
  
  markup = cmd
  
  ################################################
  # START of conversion
  ################################################
  
  # Initialise placeholder storage
  placeholder_storage = PlaceholderStorage()
  markup = placeholder_storage.replace_marker_with_placeholder(markup)
  
  # Process placeholder-protected syntax
  # (i.e. syntax where the content is protected by a placeholder)
  markup = process_literals(placeholder_storage, markup)
  markup = process_display_code(placeholder_storage, markup)
  markup = process_inline_code(placeholder_storage, markup)
  markup = process_comments(markup)
  markup = process_display_maths(placeholder_storage, markup)
  markup = process_inline_maths(placeholder_storage, markup)
  markup = process_inclusions(placeholder_storage, cmd_name, markup)
  
  # Process regex replacement definitions
  regex_replacement_storage = RegexReplacementStorage()
  markup = process_regex_replacement_definitions(
    placeholder_storage, regex_replacement_storage, cmd_name,
    markup
  )
  markup = regex_replacement_storage.replace_patterns('A', markup)
  
  # Process ordinary replacement definitions
  ordinary_replacement_storage = OrdinaryReplacementStorage()
  markup = process_ordinary_replacement_definitions(
    ordinary_replacement_storage, cmd_name,
    markup
  )
  markup = ordinary_replacement_storage.replace_patterns('A', markup)
  
  # Process preamble
  markup = regex_replacement_storage.replace_patterns('p', markup)
  markup = ordinary_replacement_storage.replace_patterns('p', markup)
  property_storage = PropertyStorage()
  markup = process_preamble(
    placeholder_storage, property_storage, cmd_name,
    markup
  )
  
  # Process blocks
  markup = regex_replacement_storage.replace_patterns('b', markup)
  markup = ordinary_replacement_storage.replace_patterns('b', markup)
  markup = process_blocks(placeholder_storage, markup)
  
  # Process tables
  markup = regex_replacement_storage.replace_patterns('t', markup)
  markup = ordinary_replacement_storage.replace_patterns('t', markup)
  markup = process_tables(placeholder_storage, markup)
  
  # Process escapes
  markup = regex_replacement_storage.replace_patterns('e', markup)
  markup = ordinary_replacement_storage.replace_patterns('e', markup)
  markup = process_escapes(placeholder_storage, markup)
  
  # Process line continuations
  markup = regex_replacement_storage.replace_patterns('c', markup)
  markup = ordinary_replacement_storage.replace_patterns('c', markup)
  markup = process_line_continuations(markup)
  
  # Process reference-style definitions
  markup = regex_replacement_storage.replace_patterns('r', markup)
  markup = ordinary_replacement_storage.replace_patterns('r', markup)
  reference_storage = ReferenceStorage()
  markup = process_reference_definitions(
    placeholder_storage, reference_storage, markup
  )
  
  # Process images
  markup = regex_replacement_storage.replace_patterns('i', markup)
  markup = ordinary_replacement_storage.replace_patterns('i', markup)
  markup = process_images(placeholder_storage, reference_storage, markup)
  
  # Process links
  markup = regex_replacement_storage.replace_patterns('l', markup)
  markup = ordinary_replacement_storage.replace_patterns('l', markup)
  markup = process_links(placeholder_storage, reference_storage, markup)
  
  # Process headings
  markup = regex_replacement_storage.replace_patterns('h', markup)
  markup = ordinary_replacement_storage.replace_patterns('h', markup)
  markup = process_headings(placeholder_storage, markup)
  
  # Process inline semantics
  markup = regex_replacement_storage.replace_patterns('s', markup)
  markup = ordinary_replacement_storage.replace_patterns('s', markup)
  markup = process_inline_semantics(placeholder_storage, markup)
  
  # Process whitespace
  markup = regex_replacement_storage.replace_patterns('w', markup)
  markup = ordinary_replacement_storage.replace_patterns('w', markup)
  markup = process_whitespace(markup)
  
  # Replace placeholders strings with markup portions
  markup = regex_replacement_storage.replace_patterns('Z', markup)
  markup = ordinary_replacement_storage.replace_patterns('Z', markup)
  markup = placeholder_storage.replace_placeholders_with_markup(markup)
  
  ################################################
  # END of conversion
  ################################################
  
  html = markup
  
  return html


################################################################
# Wrappers
################################################################


def cmd_file_to_html_file(cmd_name):
  """
  Run converter on CMD file and generate HTML file.
  """
  
  # Canonicalise file name:
  # (1) Convert Windows backslashes to forward slashes
  # (2) Remove leading dot-slash for current directory
  # (3) Remove trailing "." or ".cmd" extension if given
  cmd_name = re.sub(r'\\', '/', cmd_name)
  cmd_name = re.sub(r'\A[.]/', '', cmd_name)
  cmd_name = re.sub(r'[.](cmd)?\Z', '', cmd_name)
  
  # Read CMD from CMD file
  try:
    with open(f'{cmd_name}.cmd', 'r', encoding='utf-8') as cmd_file:
      cmd = cmd_file.read()
  except FileNotFoundError as file_not_found_error:
    error_message = join_staggered(2,
      f"CMD file '{cmd_name}.cmd' not found:",
        str(file_not_found_error)
    )
    raise FileNotFoundError(error_message) from file_not_found_error
  
  # Convert CMD to HTML
  html = cmd_to_html(cmd, cmd_name)
  
  # Write HTML to HTML file
  with open(f'{cmd_name}.html', 'w', encoding='utf-8') as html_file:
    html_file.write(html)


def main(cmd_name):
  
  # Read CMD ignore patterns from .cmdignore
  try:
    with open('.cmdignore', 'r', encoding='utf-8') as cmd_ignore_file:
      cmd_ignore_content = cmd_ignore_file.read()
  except FileNotFoundError:
    cmd_ignore_content = ''
  
  # Convert to a list and ensure leading ./
  cmd_ignore_pattern_list = cmd_ignore_content.split()
  cmd_ignore_pattern_list = [
    re.sub('\A(?![.]/)', './', cmd_ignore_pattern)
      for cmd_ignore_pattern in cmd_ignore_pattern_list
  ]
  
  # Get list of CMD files to be converted
  if cmd_name == '':
    # Get list of all CMD files
    cmd_name_list = [
      os.path.join(path, name)
        for path, _, files in os.walk('.')
          for name in files
            if fnmatch.fnmatch(name, '*.cmd')
    ]
    # Filter out ignored CMD files
    cmd_name_list = [
      cmd_name
        for cmd_name in cmd_name_list
          if not any(
            fnmatch.fnmatch(cmd_name, cmd_ignore_pattern)
              for cmd_ignore_pattern in cmd_ignore_pattern_list
          )
    ]
  else:
    cmd_name_list = [cmd_name]
  
  # Convert CMD files
  for cmd_name in cmd_name_list:
    cmd_file_to_html_file(cmd_name)


if __name__ == '__main__':
  
  DESCRIPTION_TEXT = '''
    Convert Conway's markdown (CMD) to HTML.
  '''
  parser = argparse.ArgumentParser(description=DESCRIPTION_TEXT)
  
  CMD_NAME_HELP_TEXT = '''
    Name of CMD file to be converted.
    Output is cmd_name.html.
    Omit to convert all CMD files,
    except those listed in .cmdignore.
  '''
  parser.add_argument(
    'cmd_name',
    help=CMD_NAME_HELP_TEXT,
    metavar='cmd_name[.[cmd]]',
    nargs='?',
    default=''
  )
  
  arguments = parser.parse_args()
  
  cmd_name = arguments.cmd_name
  
  main(cmd_name)
