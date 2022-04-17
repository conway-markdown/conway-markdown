#!/usr/bin/env python3

"""
# test_cmd.py

Perform unit testing for `cmd.py`.
Licensed under "MIT No Attribution" (MIT-0), see LICENSE.
"""


import cmd
import os
import unittest


class TestCmd(unittest.TestCase):
  
  def test_placeholder_master(self):
    placeholder_master = cmd.PlaceholderMaster()
    strings = [
      'The quick brown fox jumps over the lazy dog, saith he.',
      'Whoso saith \uE000, even \uF8FF\uE043\uE963\uF8FF, is wrong.',
      'What about \uE069\uE420\uE000\uF8FE\uE064?',
    ]
    placeholders = [
      placeholder_master.protect(
        placeholder_master.replace_marker_occurrences(string)
      )
        for string in strings
    ]
    self.assertEqual(
      ''.join(strings),
      placeholder_master.unprotect(''.join(placeholders))
    )
  
  def test_placeholder_master_encode_digit(self):
    self.assertEqual(
      cmd.PlaceholderMaster.encode_digit(0),
      '\uE000'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode_digit(1),
      '\uE001'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode_digit(0x69),
      '\uE069'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode_digit(0x420),
      '\uE420'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode_digit(0x18FE),
      '\uF8FE'
    )
  
  def test_placeholder_master_decode_encoded_digit(self):
    self.assertEqual(
      cmd.PlaceholderMaster.decode_encoded_digit('\uE000'),
      0
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode_encoded_digit('\uE001'),
      1
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode_encoded_digit('\uE069'),
      0x69
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode_encoded_digit('\uE420'),
      0x420
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode_encoded_digit('\uF8FE'),
      0x18FE
    )
  
  def test_placeholder_master_encode(self):
    self.assertEqual(
      cmd.PlaceholderMaster.encode(0),
      '\uE000'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode(1),
      '\uE001'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode(0x18FE),
      '\uF8FE'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode(0x18FF),
      '\uE001\uE000'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode(0x69420),
      '\uE043\uE963'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode(
          0x0069 * 0x18FF ** 4
        + 0x0420 * 0x18FF ** 3
        + 0x18FE * 0x18FF ** 1
        + 0x0064 * 0x18FF ** 0
      ),
      '\uE069\uE420\uE000\uF8FE\uE064'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.encode(0x18FF ** 50 + 0x89 * 0x18FF),
      '\uE001' + 48 * '\uE000' + '\uE089' + '\uE000'
    )
  
  def test_placeholder_master_decode(self):
    self.assertEqual(
      cmd.PlaceholderMaster.decode('\uE000'),
      0
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode('\uE001'),
      1
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode('\uF8FE'),
      0x18FE
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode('\uE001\uE000'),
      0x18FF
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode('\uE043\uE963'),
      0x69420
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode('\uE069\uE420\uE000\uF8FE\uE064'),
      0x0069 * 0x18FF ** 4
      + 0x0420 * 0x18FF ** 3
      + 0x18FE * 0x18FF ** 1
      + 0x0064 * 0x18FF ** 0
    )
    self.assertEqual(
      cmd.PlaceholderMaster.decode(
        '\uE001' + 48 * '\uE000' + '\uE089' + '\uE000'
      ),
      0x18FF ** 50 + 0x89 * 0x18FF
    )
  
  def test_placeholder_master_build_placeholder(self):
    self.assertEqual(
      cmd.PlaceholderMaster.build_placeholder(0),
      '\uF8FF\uE000\uF8FF'
    )
    self.assertEqual(
      cmd.PlaceholderMaster.build_placeholder(0x69420),
      '\uF8FF\uE043\uE963\uF8FF'
    )
  
  def test_ordinary_dictionary_replacement_build_regex_pattern(self):
    
    self.assertEqual(
      cmd.OrdinaryDictionaryReplacement.build_regex_pattern({}),
      ''
    )
    self.assertEqual(
      cmd.OrdinaryDictionaryReplacement.build_regex_pattern(
        {
          'a': 'b',
          'b': 'c',
          'c': 'd',
          r'#$&*+-.^\|~': 'COMPLICATED',
        }
      ),
      r'a|b|c|\#\$\&\*\+\-\.\^\\\|\~'
    )
  
  def test_extensible_fence_replacement_build_regex_pattern(self):
    
    self.assertEqual(
      cmd.ExtensibleFenceReplacement.build_regex_pattern(
        syntax_is_block=False,
        flag_name_from_letter={
          'u': 'KEEP_HTML_UNESCAPED',
          'i': 'KEEP_INDENTED',
        },
        has_flags=True,
        opening_delimiter='{',
        extensible_delimiter_character='+',
        extensible_delimiter_min_count=2,
        attribute_specifications=None,
        closing_delimiter='}',
      ),
      '(?P<flags> [ui]* )'
      r'\{'
      r'(?P<extensible_delimiter> \+{2,} )'
      r'(?P<content> [\s\S]*? )'
      '(?P=extensible_delimiter)'
      r'\}'
    )
    
    self.assertEqual(
      cmd.ExtensibleFenceReplacement.build_regex_pattern(
        syntax_is_block=True,
        flag_name_from_letter={},
        has_flags=False,
        opening_delimiter='',
        extensible_delimiter_character='$',
        extensible_delimiter_min_count=4,
        attribute_specifications='',
        closing_delimiter='',
      ),
      r'^[^\S\n]*'
      r'(?P<extensible_delimiter> \${4,} )'
      r'(?: \{ (?P<attribute_specifications> [^}]*? ) \} )?'
      r'\n'
      r'(?P<content> [\s\S]*? )'
      r'^[^\S\n]*'
      '(?P=extensible_delimiter)'
    )
  
  def test_compute_longest_common_prefix(self):
    self.assertEqual(
      cmd.compute_longest_common_prefix(['a', 'b', 'c', 'd']),
      ''
    )
    self.assertEqual(
      cmd.compute_longest_common_prefix(['  ', '  ', '   ', '      ']),
      '  '
    )
    self.assertEqual(
      cmd.compute_longest_common_prefix(['\t  ', '\t  3', '\t   \t \t']),
      '\t  '
    )
  
  def test_de_indent(self):
    self.assertEqual(
      cmd.de_indent(
'''
    4 spaces

      4 spaces + 2 spaces
      \t   4 spaces + 2 spaces, 1 tab, 3 spaces
     
     4 spaces + 1 space (this line and above)
'''
      ),
'''
4 spaces

  4 spaces + 2 spaces
  \t   4 spaces + 2 spaces, 1 tab, 3 spaces
 
 4 spaces + 1 space (this line and above)
'''
    )
    self.assertEqual(
      cmd.de_indent(
        '''
\t\t \t\t\t\t\t\t And,
\t\t \t\t\t\t\t\tWhitespace before closing delimiter:
        '''
      ),
'''
 And,
Whitespace before closing delimiter:
'''
    )
  
  def test_build_attributes_sequence(self):
    self.assertEqual(
      cmd.build_attributes_sequence(''),
      ''
    )
    self.assertEqual(
      cmd.build_attributes_sequence('  '),
      ''
    )
    self.assertEqual(
      cmd.build_attributes_sequence('\t'),
      ''
    )
    self.assertEqual(
      cmd.build_attributes_sequence('   \n name=value\n    '),
      ' name="value"'
    )
    self.assertEqual(
      cmd.build_attributes_sequence(' empty1="" empty2=  boolean'),
      ' empty1="" empty2="" boolean'
    )
    self.assertEqual(
      cmd.build_attributes_sequence('qv="quoted value" bv=bare-value'),
      ' qv="quoted value" bv="bare-value"'
    )
    self.assertEqual(
      cmd.build_attributes_sequence('#=top .=good    l=en    r=3    c=2'),
      ' id="top" class="good" lang="en" rowspan="3" colspan="2"'
    )
    self.assertEqual(
      cmd.build_attributes_sequence('id=x #y .a .b name=value .=c class="d"'),
      ' id="y" class="a b c d" name="value"'
    )
    self.assertEqual(
      cmd.build_attributes_sequence('w="320" h=16 s="font-weight: bold"'),
      ' width="320" height="16" style="font-weight: bold"'
    )
  
  def test_build_flags_regex(self):
    self.assertEqual(cmd.build_flags_regex({}, False), '')
    self.assertEqual(
      cmd.build_flags_regex(
        {
          'u': 'KEEP_HTML_UNESCAPED',
          'w': 'REDUCE_WHITESPACE',
          'i': 'KEEP_INDENTED',
        },
        True,
      ),
      '(?P<flags> [uwi]* )'
    )
  
  def test_build_extensible_delimiter_opening_regex(self):
    self.assertEqual(
      cmd.build_extensible_delimiter_opening_regex('$', 5),
      r'(?P<extensible_delimiter> \${5,} )'
    )
  
  def test_none_to_empty_string(self):
    self.assertEqual(cmd.none_to_empty_string(''), '')
    self.assertEqual(cmd.none_to_empty_string(None), '')
    self.assertEqual(cmd.none_to_empty_string('xyz'), 'xyz')
  
  def test_extract_rules_and_content(self):
    self.assertEqual(cmd.extract_rules_and_content(''), (None, ''))
    self.assertEqual(cmd.extract_rules_and_content('abc'), (None, 'abc'))
    self.assertEqual(cmd.extract_rules_and_content('%%%abc'), (None, '%%%abc'))
    self.assertEqual(cmd.extract_rules_and_content('abc%%%'), (None, 'abc%%%'))
    self.assertEqual(cmd.extract_rules_and_content('%%%\nabc'), ('', 'abc'))
    self.assertEqual(cmd.extract_rules_and_content('X%%\nY'), (None, 'X%%\nY'))
    self.assertEqual(
      cmd.extract_rules_and_content(
        'This be the preamble.\nEven two lines of preamble.\n%%%%%\nYea.\n'
      ),
      ('This be the preamble.\nEven two lines of preamble.\n', 'Yea.\n')
    )
    self.assertEqual(
      cmd.extract_rules_and_content(
        'ABC\n%%%\n123\n%%%%%%%\nXYZ'
      ),
      ('ABC\n', '123\n%%%%%%%\nXYZ')
    )
  
  def test_cmd_to_html(self):
    
    self.assertEqual(
      cmd.cmd_to_html(
        ################################################################
        # START CMD
        ################################################################
r'''%%%

# `test_cmd_to_html`

## `#placeholder-markers`

If implemented properly, the following shall confound not:
  `'\uF8FF\uE069\uE420\uE000\uF8FE\uE064\uF8FF'`: 

## `#literals`

BEFORE{ <`` Literal & < > ``> }AFTER
    <```
      No indent,
          yet four more spaces hence?
    ```>
   u<```` Flag `u`: unescaped HTML, <b>&amp; for ampersand!</b> ````>
   i<```
          Flag `i`: whitespace stripped on this line,
      but indent preserved on lines thereafter.
    ```>
   uw<````````
      Flag `w`: whitespace trimmed on all lines,
        even trailing whitespace,  
          and even whitespace before a break element:        <br>
    ````````>

## `#display-code`

  ```
    for (int index = 0; index < count; index++)
    {
      // etc. etc.
    }
  ```
  ````{#display-code-1 .class-2 l=en lang=en-AU .class-3}
    :(
    u<``<strong>LITERALS PREVAIL OVER DISPLAY CODE</strong>``>
        <```<``(unless restrained by an outer literal)``>```>
  ````
  i```
    Retained indentation:
      a lot.
   ```
'''
        ################################################################
        # END CMD
        ################################################################
        ,
        'test_cmd.py'
      ),
      ################################################################
      # START HTML
      ################################################################
r'''
# `test_cmd_to_html`
## `#placeholder-markers`
If implemented properly, the following shall confound not:
`'\uF8FF\uE069\uE420\uE000\uF8FE\uE064\uF8FF'`: 
## `#literals`
BEFORE{ Literal &amp; &lt; &gt; }AFTER
No indent,
    yet four more spaces hence?
Flag `u`: unescaped HTML, <b>&amp; for ampersand!</b>
Flag `i`: whitespace stripped on this line,
      but indent preserved on lines thereafter.
Flag `w`: whitespace trimmed on all lines,
even trailing whitespace,
and even whitespace before a break element:<br>
## `#display-code`
<pre><code>for (int index = 0; index &lt; count; index++)
{
  // etc. etc.
}
</code></pre>
<pre id="display-code-1" class="class-2 class-3" lang="en-AU"><code>:(
<strong>LITERALS PREVAIL OVER DISPLAY CODE</strong>
    &lt;``(unless restrained by an outer literal)``&gt;
</code></pre>
<pre><code>    Retained indentation:
      a lot.
</code></pre>
'''
      ################################################################
      # END HTML
      ################################################################
    )
    
    self.assertEqual(
      cmd.cmd_to_html(
r'''
RegexDictionaryReplacement: #delete-everything
- queue_position: AFTER #placeholder-unprotect
* [\s\S]* -->

%%%

The quick brown fox jumps over the lazy dog.
Everything is everything, and everything is dumb.
'''
        ,
        'test_cmd.py'
      ),
      ''
    )
  
  def test_is_cmd_file(self):
    self.assertTrue(cmd.is_cmd_file('file.cmd'))
    self.assertTrue(cmd.is_cmd_file('.cmd'))
    self.assertFalse(cmd.is_cmd_file('file/cmd'))
    self.assertFalse(cmd.is_cmd_file('file.'))
    self.assertFalse(cmd.is_cmd_file('file'))
  
  def test_extract_cmd_name(self):
    
    self.assertEqual(cmd.extract_cmd_name('file.cmd'), 'file')
    self.assertEqual(cmd.extract_cmd_name('file.'), 'file')
    self.assertEqual(cmd.extract_cmd_name('file'), 'file')
    
    if os.sep == '/':
      self.assertEqual(cmd.extract_cmd_name('./././file.cmd'), 'file')
      self.assertEqual(cmd.extract_cmd_name('./dir/../file.cmd'), 'file')
      self.assertEqual(cmd.extract_cmd_name('./file.'), 'file')
      self.assertEqual(cmd.extract_cmd_name('./file'), 'file')
    elif os.sep == '\\':
      self.assertEqual(cmd.extract_cmd_name(r'.\.\.\file.cmd'), 'file')
      self.assertEqual(cmd.extract_cmd_name(r'.\dir\..\file.cmd'), 'file')
      self.assertEqual(cmd.extract_cmd_name(r'.\file.'), 'file')
      self.assertEqual(cmd.extract_cmd_name(r'.\file'), 'file')


if __name__ == '__main__':
  unittest.main()
