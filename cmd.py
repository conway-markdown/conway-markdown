#!/usr/bin/env python3

"""
# cmd.py

Convert Conway-Markdown (CMD) to HTML.
Licensed under "MIT No Attribution" (MIT-0), see LICENSE.

CMD files are parsed as
````
«replacement_rules»
«delimiter»
«main_content»
````
where «delimiter» is the first occurrence of 3-or-more percent signs on its own line.
If the file is free of «delimiter», the whole file is parsed as «main_content».

For details on how «replacement_rules» and «main_content» are parsed, see <https://conway-markdown.github.io/>.
"""

import abc
import argparse
import copy
import os
import re
import sys
import traceback
import warnings

__version__ = '4.6.0'

GENERIC_ERROR_EXIT_CODE = 1
COMMAND_LINE_ERROR_EXIT_CODE = 2
VERBOSE_MODE_DIVIDER_SYMBOL_COUNT = 48


class CommittedMutateException(Exception):
    pass


class UncommittedApplyException(Exception):
    pass


class MissingAttributeException(Exception):
    def __init__(self, missing_attribute):
        self._missing_attribute = missing_attribute

    @property
    def missing_attribute(self):
        return self._missing_attribute


class UnrecognisedLabelException(Exception):
    pass


class PlaceholderMaster:
    """
    Static class providing placeholder protection to strings.

    There are many instances in which the result of a replacement
    should not be altered further by replacements to follow.
    To protect a string from further alteration,
    it is temporarily replaced by a placeholder
    consisting of code points in the main Unicode Private Use Area.
    Specifically, the placeholder shall be of the form
    `«marker»«run_characters»«marker»`, where «marker» is `U+F8FF`,
    and «run_characters» are between `U+E000` and `U+E0FF`
    each representing a Unicode byte of the string.

    The very first call to PlaceholderMaster should be to the
    `replace_marker_occurrences(...)` method;
    this replaces occurrences of «marker» themselves with a placeholder,
    lest those occurrences of «marker» be confounding.
    The very last call to PlaceholderMaster should be to unprotect
    the text (restoring the strings were protected with a placeholder).

    It is assumed the user will not define replacement rules that
    tamper with strings of the form `«marker»«run_characters»«marker»`.
    Note that the user should not be using Private Use Area code points
    in the first place, see <https://www.w3.org/TR/charmod/#C073>.
    """
    def __new__(cls):
        raise TypeError('PlaceholderMaster cannot be instantiated')

    _RUN_CHARACTER_MIN = '\uE000'
    _RUN_CHARACTER_MAX = '\uE100'
    MARKER = '\uF8FF'
    _REPLACEMENT_CHARACTER = '\uFFFD'

    _RUN_CODE_POINT_MIN = ord(_RUN_CHARACTER_MIN)
    _REPLACEMENT_CODE_POINT = ord(_REPLACEMENT_CHARACTER)

    _PLACEHOLDER_PATTERN_COMPILED = \
        re.compile(
            f'{MARKER} (?P<run_characters> [{_RUN_CHARACTER_MIN}-{_RUN_CHARACTER_MAX}]* ) {MARKER}',
            flags=re.VERBOSE,
        )

    @staticmethod
    def _unprotect_substitute_function(placeholder_match):
        run_characters = placeholder_match.group('run_characters')
        string_bytes = \
            bytes(
                ord(character) - PlaceholderMaster._RUN_CODE_POINT_MIN
                for character in run_characters
            )

        try:
            string = string_bytes.decode()
        except UnicodeDecodeError:
            warnings.warn(
                f'warning: placeholder encountered with run characters '
                f'representing invalid byte sequence {string_bytes}; '
                f'substituted with U+{PlaceholderMaster._REPLACEMENT_CODE_POINT:X} '
                f'REPLACEMENT CHARACTER as a fallback\n\n'
                f'Possible causes:\n'
                f'- Confounding occurrences of «marker» have not been removed '
                f'by calling PlaceholderMaster.replace_marker_occurrences(...)\n'
                f'- A replacement rule has been defined that tampers with '
                f'strings of the form `«marker»«run_characters»«marker»`'
            )
            string = PlaceholderMaster._REPLACEMENT_CHARACTER

        return string

    @staticmethod
    def replace_marker_occurrences(string):
        """
        Replace occurrences of «marker» with a placeholder.

        The intent here is to ensure that occurrences of «marker»
        will not be confounding by the time `unprotect(...)` is called.
        It just so happens that the act of replacing occurrences
        of «marker» is equivalent to protecting them with a placeholder.
        """
        return re.sub(
            PlaceholderMaster.MARKER,
            PlaceholderMaster.protect(PlaceholderMaster.MARKER),
            string,
        )

    @staticmethod
    def protect(string):
        """
        Protect a string by converting it to a placeholder.
        """
        marker = PlaceholderMaster.MARKER

        string = PlaceholderMaster.unprotect(string)
        string_bytes = string.encode()
        run_characters = \
            ''.join(
                chr(byte + PlaceholderMaster._RUN_CODE_POINT_MIN)
                for byte in string_bytes
            )

        placeholder = f'{marker}{run_characters}{marker}'

        return placeholder

    @staticmethod
    def unprotect(string):
        """
        Unprotect a string by restoring placeholders to their strings.
        """
        return re.sub(
            PlaceholderMaster._PLACEHOLDER_PATTERN_COMPILED,
            PlaceholderMaster._unprotect_substitute_function,
            string,
        )


class Reference:
    """
    A reference to be used by links and images.

    For a given «label» (normalised to lower case),
    a reference consists of
    - «attribute specifications»
    - «uri»
    - «title»
    where «uri» is `href` for links and `src` for images.
    """
    def __init__(self, attribute_specifications, uri, title):
        self._attribute_specifications = attribute_specifications
        self._uri = uri
        self._title = title

    @property
    def attribute_specifications(self):
        return self._attribute_specifications

    @property
    def uri(self):
        return self._uri

    @property
    def title(self):
        return self._title


class ReferenceMaster:
    """
    Object storing references to be used by links and images.
    """
    def __init__(self):
        self._reference_from_label = {}

    def store_definition(self, label, attribute_specifications, uri, title):

        label = ReferenceMaster.normalise_label(label)
        self._reference_from_label[label] = Reference(attribute_specifications, uri, title)

    def load_definition(self, label):
        label = ReferenceMaster.normalise_label(label)

        try:
            reference = self._reference_from_label[label]
        except KeyError:
            raise UnrecognisedLabelException

        attribute_specifications = reference.attribute_specifications
        uri = reference.uri
        title = reference.title

        return attribute_specifications, uri, title

    @staticmethod
    def normalise_label(label):
        return label.lower()


class Replacement(abc.ABC):
    """
    Base class for a replacement rule.

    Not to be used when authoring CMD documents.
    (Hypothetical) CMD replacement rule syntax:
    ````
    Replacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - positive_flag: (def) NONE | «FLAG_NAME»
    - negative_flag: (def) NONE | «FLAG_NAME»
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        self._is_committed = False
        self._id = id_
        self._queue_position_type = None
        self._queue_reference_replacement = None
        self._positive_flag_name = None
        self._negative_flag_name = None
        self._verbose_mode_enabled = verbose_mode_enabled

    @property
    @abc.abstractmethod
    def attribute_names(self):
        raise NotImplementedError

    @property
    def id_(self):
        return self._id

    @property
    def queue_position_type(self):
        return self._queue_position_type

    @queue_position_type.setter
    def queue_position_type(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `queue_position_type` after `commit()`')

        self._queue_position_type = value

    @property
    def queue_reference_replacement(self):
        return self._queue_reference_replacement

    @queue_reference_replacement.setter
    def queue_reference_replacement(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `queue_reference_replacement` after `commit()`')

        self._queue_reference_replacement = value

    @property
    def positive_flag_name(self):
        return self._positive_flag_name

    @positive_flag_name.setter
    def positive_flag_name(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `positive_flag_name` after `commit()`')

        self._positive_flag_name = value

    @property
    def negative_flag_name(self):
        return self._negative_flag_name

    @negative_flag_name.setter
    def negative_flag_name(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `negative_flag_name` after `commit()`')

        self._negative_flag_name = value

    def commit(self):
        self._validate_mandatory_attributes()
        self._set_apply_method_variables()
        self._is_committed = True

    def apply(self, string, enabled_flag_names=None):
        if not self._is_committed:
            raise UncommittedApplyException('error: cannot call `apply(string)` before `commit()`')

        if enabled_flag_names is not None:
            positive_flag_name = self._positive_flag_name
            if positive_flag_name is not None and positive_flag_name not in enabled_flag_names:
                return string

            negative_flag_name = self._negative_flag_name
            if negative_flag_name is not None and negative_flag_name in enabled_flag_names:
                return string

        string_before = string
        string = self._apply(string)
        string_after = string

        if self._verbose_mode_enabled:
            if string_before == string_after:
                no_change_indicator = ' (no change)'
            else:
                no_change_indicator = ''

            try:
                print('<' * VERBOSE_MODE_DIVIDER_SYMBOL_COUNT + f' BEFORE #{self._id}')
                print(string_before)
                print('=' * VERBOSE_MODE_DIVIDER_SYMBOL_COUNT + no_change_indicator)
                print(string_after)
                print('>' * VERBOSE_MODE_DIVIDER_SYMBOL_COUNT + f' AFTER #{self._id}')
                print('\n\n\n\n')
            except UnicodeEncodeError as unicode_encode_error:
                # caused by Private Use Area code points used for placeholders
                error_message = \
                    (
                        'bad print due to non-Unicode terminal encoding, '
                        'likely `cp1252` on Git BASH for Windows. '
                        'Try setting the `PYTHONIOENCODING` environment variable '
                        'to `utf-8` (add `export PYTHONIOENCODING=utf-8` '
                        'to `.bash_profile` and then source it). '
                        'See <https://stackoverflow.com/a/7865013>.'
                    )
                raise FileNotFoundError(error_message) from unicode_encode_error

        return string_after

    @abc.abstractmethod
    def _validate_mandatory_attributes(self):
        """
        Ensure all mandatory attributes have been set.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def _set_apply_method_variables(self):
        """
        Set variables used in `self._apply(string)`.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def _apply(self, string):
        """
        Apply the defined replacement to a string.
        """
        raise NotImplementedError


class ReplacementWithSubstitutions(Replacement, abc.ABC):
    """
    Base class for a replacement rule with substitutions.

    Not to be used when authoring CMD documents.
    (Hypothetical) CMD replacement rule syntax:
    ````
    ReplacementWithSubstitutions: #«id»
    * "«pattern»" | '«pattern»' | «pattern»
        -->
      CMD_VERSION | CMD_NAME | CMD_BASENAME | CLEAN_URL |
              "«substitute»" | '«substitute»' | «substitute»
    [...]
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._substitute_from_pattern = {}

    def add_substitution(self, pattern, substitute):
        if self._is_committed:
            raise CommittedMutateException(
                'error: cannot call `add_substitution(...)` after `commit()`'
            )
        self._substitute_from_pattern[pattern] = substitute


class ReplacementWithSyntaxType(Replacement, abc.ABC):
    """
    Base class for a replacement rule with `syntax_type`.

    Not to be used when authoring CMD documents.
    (Hypothetical) CMD replacement rule syntax:
    ````
    ReplacementWithSyntaxType: #«id»
    - syntax_type: BLOCK | INLINE (mandatory)
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._syntax_type_is_block = None

    @property
    def syntax_type_is_block(self):
        return self._syntax_type_is_block

    @syntax_type_is_block.setter
    def syntax_type_is_block(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `syntax_type_is_block` after `commit()`')

        self._syntax_type_is_block = value


class ReplacementWithAllowedFlags(Replacement, abc.ABC):
    """
    Base class for a replacement rule with `allowed_flags`.

    Not to be used when authoring CMD documents.
    (Hypothetical) CMD replacement rule syntax:
    ````
    ReplacementWithAllowedFlags: #«id»
    - allowed_flags: (def) NONE | «letter»=«FLAG_NAME» [...]
    ````
    """

    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._flag_name_from_letter = {}
        self._has_flags = False

    @property
    def flag_name_from_letter(self):
        return self._flag_name_from_letter

    @flag_name_from_letter.setter
    def flag_name_from_letter(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `flag_name_from_letter` after `commit()`')
        self._flag_name_from_letter = copy.copy(value)

    @staticmethod
    def get_enabled_flag_names(match, flag_name_from_letter, has_flags):
        enabled_flag_names = set()
        if has_flags:
            flags = match.group('flags')
            for flag_letter, flag_name in flag_name_from_letter.items():
                if flag_letter in flags:
                    enabled_flag_names.add(flag_name)

        return enabled_flag_names


class ReplacementWithAttributeSpecifications(Replacement, abc.ABC):
    """
    Base class for a replacement rule with `attribute_specifications`.

    Not to be used when authoring CMD documents.
    (Hypothetical) CMD replacement rule syntax:
    ````
    ReplacementWithAttributeSpecifications: #«id»
    - attribute_specifications: (def) NONE | EMPTY | «string»
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._attribute_specifications = None

    @property
    def attribute_specifications(self):
        return self._attribute_specifications

    @attribute_specifications.setter
    def attribute_specifications(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `attribute_specifications` after `commit()`')

        self._attribute_specifications = value


class ReplacementWithProhibitedContent(Replacement, abc.ABC):
    """
    Base class for a replacement rule with `prohibited_content`.

    Not to be used when authoring CMD documents.
    (Hypothetical) CMD replacement rule syntax:
    ````
    ReplacementWithProhibitedContent: #«id»
    - prohibited_content: (def) NONE | BLOCKS | ANCHORED_BLOCKS
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._prohibited_content_regex = None

    @property
    def prohibited_content_regex(self):
        return self._prohibited_content_regex

    @prohibited_content_regex.setter
    def prohibited_content_regex(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `prohibited_content_regex` after `commit()`')

        self._prohibited_content_regex = value


class ReplacementWithContentReplacements(Replacement, abc.ABC):
    """
    Base class for a replacement rule with `content_replacements`.

    Not to be used when authoring CMD documents.
    (Hypothetical) CMD replacement rule syntax:
    ````
    ReplacementWithContentReplacements: #«id»
    - content_replacements: (def) NONE | #«id» [...]
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._content_replacements = []

    @property
    def content_replacements(self):
        return self._content_replacements

    @content_replacements.setter
    def content_replacements(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `content_replacements` after `commit()`')

        self._content_replacements = copy.copy(value)


class ReplacementWithTagName(Replacement, abc.ABC):
    """
    Base class for a replacement rule with `tag_name`.

    Not to be used when authoring CMD documents.
    (Hypothetical) CMD replacement rule syntax:
    ````
    ReplacementWithTagName: #«id»
    - tag_name: (def) NONE | «name»
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._tag_name = None

    @property
    def tag_name(self):
        return self._tag_name

    @tag_name.setter
    def tag_name(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `tag_name` after `commit()`')

        self._tag_name = value


class ReplacementWithConcludingReplacements(Replacement, abc.ABC):
    """
    Base class for a replacement rule with `concluding_replacements`.

    Not to be used when authoring CMD documents.
    (Hypothetical) CMD replacement rule syntax:
    ````
    ReplacementWithConcludingReplacements: #«id»
    - concluding_replacements: (def) NONE | #«id» [...]
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._concluding_replacements = []

    @property
    def concluding_replacements(self):
        return self._concluding_replacements

    @concluding_replacements.setter
    def concluding_replacements(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `concluding_replacements` after `commit()`')

        self._concluding_replacements = copy.copy(value)


class ReplacementSequence(Replacement):
    """
    A replacement rule that applies a sequence of replacement rules.

    CMD replacement rule syntax:
    ````
    ReplacementSequence: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - replacements: (def) NONE | #«id» [...]
    ````
    """

    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._replacements = []

    def attribute_names(self):
        return (
            'queue_position',
            'replacements',
        )

    @property
    def replacements(self):
        return self._replacements

    @replacements.setter
    def replacements(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `replacements` after `commit()`')

        self._replacements = copy.copy(value)

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        pass

    def _apply(self, string):
        for replacement in self._replacements:
            string = replacement.apply(string)

        return string


class PlaceholderMarkerReplacement(Replacement):
    """
    A rule for replacing the placeholder marker with a placeholder.

    Ensures that occurrences of «marker» will not be confounding.
    To be used before PlaceholderProtectionReplacement.
    See class PlaceholderMaster, especially `replace_marker_occurrences`.

    CMD replacement rule syntax:
    ````
    PlaceholderMarkerReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)

    def attribute_names(self):
        return (
            'queue_position',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        pass

    def _apply(self, string):
        return PlaceholderMaster.replace_marker_occurrences(string)


class PlaceholderProtectionReplacement(Replacement):
    """
    A replacement rule for protecting strings with a placeholder.

    CMD replacement rule syntax:
    ````
    PlaceholderProtectionReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    ````
    """

    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)

    def attribute_names(self):
        return (
            'queue_position',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        pass

    def _apply(self, string):
        return PlaceholderMaster.protect(string)


class PlaceholderUnprotectionReplacement(Replacement):
    """
    A replacement rule for restoring placeholders to their strings.

    CMD replacement rule syntax:
    ````
    PlaceholderUnprotectionReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)

    def attribute_names(self):
        return (
            'queue_position',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        pass

    def _apply(self, string):
        return PlaceholderMaster.unprotect(string)


class DeIndentationReplacement(Replacement):
    """
    A replacement rule for de-indentation.

    CMD replacement rule syntax:
    ````
    DeIndentationReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - positive_flag: (def) NONE | «FLAG_NAME»
    - negative_flag: (def) NONE | «FLAG_NAME»
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)

    def attribute_names(self):
        return (
            'queue_position',
            'positive_flag',
            'negative_flag',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        pass

    def _apply(self, string):
        return de_indent(string)


class OrdinaryDictionaryReplacement(
    ReplacementWithSubstitutions,
    ReplacementWithConcludingReplacements,
    Replacement,
):
    """
    A replacement rule for a dictionary of ordinary substitutions.

    CMD replacement rule syntax:
    ````
    OrdinaryDictionaryReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - positive_flag: (def) NONE | «FLAG_NAME»
    - negative_flag: (def) NONE | «FLAG_NAME»
    - apply_mode: (def) SIMULTANEOUS | SEQUENTIAL
    * "«pattern»" | '«pattern»' | «pattern»
        -->
      CMD_VERSION | CMD_NAME | CMD_BASENAME | CLEAN_URL |
              "«substitute»" | '«substitute»' | «substitute»
    [...]
    - concluding_replacements: (def) NONE | #«id» [...]
    ````
    """

    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._apply_substitutions_simultaneously = True
        self._simultaneous_regex_pattern_compiled = None
        self._simultaneous_substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'positive_flag',
            'negative_flag',
            'apply_mode',
            'concluding_replacements',
        )

    @property
    def apply_substitutions_simultaneously(self):
        return self._apply_substitutions_simultaneously

    @apply_substitutions_simultaneously.setter
    def apply_substitutions_simultaneously(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `apply_mode` after `commit()`')

        self._apply_substitutions_simultaneously = value

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        if self._apply_substitutions_simultaneously:
            self.set_simultaneous_apply_method_variables()

    def _apply(self, string):
        if self._apply_substitutions_simultaneously:
            return self.simultaneous_apply(string)
        else:
            return self.sequential_apply(string)

    def set_simultaneous_apply_method_variables(self):
        self._simultaneous_regex_pattern_compiled = \
            re.compile(OrdinaryDictionaryReplacement.build_simultaneous_regex_pattern(self._substitute_from_pattern))
        self._simultaneous_substitute_function = \
            self.build_simultaneous_substitute_function(self._substitute_from_pattern)

    @staticmethod
    def build_simultaneous_regex_pattern(substitute_from_pattern):
        return '|'.join(
            re.escape(pattern)
            for pattern in substitute_from_pattern
        )

    def build_simultaneous_substitute_function(self, substitute_from_pattern):
        def substitute_function(match):
            pattern = match.group()
            substitute = substitute_from_pattern[pattern]

            for replacement in self._concluding_replacements:
                substitute = replacement.apply(substitute)

            return substitute

        return substitute_function

    def simultaneous_apply(self, string):
        if len(self._substitute_from_pattern) > 0:
            string = \
                re.sub(
                    self._simultaneous_regex_pattern_compiled,
                    self._simultaneous_substitute_function,
                    string,
                )

        return string

    def sequential_apply(self, string):
        for pattern, substitute in self._substitute_from_pattern.items():
            string = string.replace(pattern, substitute)

        for replacement in self._concluding_replacements:
            string = replacement.apply(string)

        return string


class RegexDictionaryReplacement(
    ReplacementWithSubstitutions,
    ReplacementWithConcludingReplacements,
    Replacement,
):
    """
    A replacement rule for a dictionary of regex substitutions.

    Substitutions are applied in order.
    Python regex syntax is used,
    with `flags=re.ASCII | re.MULTILINE | re.VERBOSE`.

    CMD replacement rule syntax:
    ````
    RegexDictionaryReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - positive_flag: (def) NONE | «FLAG_NAME»
    - negative_flag: (def) NONE | «FLAG_NAME»
    * "«pattern»" | '«pattern»' | «pattern»
        -->
      CMD_VERSION | CMD_NAME | CMD_BASENAME | CLEAN_URL |
              "«substitute»" | '«substitute»' | «substitute»
    [...]
    - concluding_replacements: (def) NONE | #«id» [...]
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._substitute_function_from_pattern = {}

    def attribute_names(self):
        return (
            'queue_position',
            'positive_flag',
            'negative_flag',
            'concluding_replacements',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        for pattern, substitute in self._substitute_from_pattern.items():
            substitute_function = self.build_substitute_function(substitute)
            self._substitute_function_from_pattern[pattern] = substitute_function

    def _apply(self, string):
        for pattern, substitute_function in self._substitute_function_from_pattern.items():
            string = \
                re.sub(
                    pattern,
                    substitute_function,
                    string,
                    flags=re.ASCII | re.MULTILINE | re.VERBOSE,
                )

        return string

    def build_substitute_function(self, substitute):
        def substitute_function(match):
            substitute_result = match.expand(substitute)

            for replacement in self._concluding_replacements:
                substitute_result = replacement.apply(substitute_result)

            return substitute_result

        return substitute_function


class FixedDelimitersReplacement(
    ReplacementWithSyntaxType,
    ReplacementWithAllowedFlags,
    ReplacementWithAttributeSpecifications,
    ReplacementWithProhibitedContent,
    ReplacementWithContentReplacements,
    ReplacementWithTagName,
    ReplacementWithConcludingReplacements,
    Replacement,
):
    """
    A fixed-delimiters replacement rule.

    ````
    FixedDelimitersReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - syntax_type: BLOCK | INLINE (mandatory)
    - allowed_flags: (def) NONE | «letter»=«FLAG_NAME» [...]
    - opening_delimiter: «string» (mandatory)
    - attribute_specifications: (def) NONE | EMPTY | «string»
    - prohibited_content: (def) NONE | BLOCKS | ANCHORED_BLOCKS
    - content_replacements: (def) NONE | #«id» [...]
    - closing_delimiter: «string» (mandatory)
    - tag_name: (def) NONE | «name»
    - concluding_replacements: (def) NONE | #«id» [...]
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._opening_delimiter = None
        self._closing_delimiter = None
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'syntax_type',
            'allowed_flags',
            'opening_delimiter',
            'attribute_specifications',
            'prohibited_content',
            'content_replacements',
            'closing_delimiter',
            'tag_name',
            'concluding_replacements',
        )

    @property
    def opening_delimiter(self):
        return self._opening_delimiter

    @opening_delimiter.setter
    def opening_delimiter(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `opening_delimiter` after `commit()`')

        self._opening_delimiter = value

    @property
    def closing_delimiter(self):
        return self._closing_delimiter

    @closing_delimiter.setter
    def closing_delimiter(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `closing_delimiter` after `commit()`')
        self._closing_delimiter = value

    def _validate_mandatory_attributes(self):
        if self._syntax_type_is_block is None:
            raise MissingAttributeException('syntax_type')

        if self._opening_delimiter is None:
            raise MissingAttributeException('opening_delimiter')

        if self._closing_delimiter is None:
            raise MissingAttributeException('closing_delimiter')

    def _set_apply_method_variables(self):
        self._has_flags = len(self._flag_name_from_letter) > 0
        self._regex_pattern_compiled = \
            re.compile(
                FixedDelimitersReplacement.build_regex_pattern(
                    self._syntax_type_is_block,
                    self._flag_name_from_letter,
                    self._has_flags,
                    self._opening_delimiter,
                    self._attribute_specifications,
                    self._prohibited_content_regex,
                    self._closing_delimiter,
                ),
                flags=re.ASCII | re.MULTILINE | re.VERBOSE,
            )
        self._substitute_function = \
            self.build_substitute_function(
                self._flag_name_from_letter,
                self._has_flags,
                self._attribute_specifications,
                self._tag_name,
            )

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(
        syntax_type_is_block,
        flag_name_from_letter,
        has_flags,
        opening_delimiter,
        attribute_specifications,
        prohibited_content_regex,
        closing_delimiter,
    ):
        block_anchoring_regex = build_block_anchoring_regex(syntax_type_is_block)
        flags_regex = build_flags_regex(flag_name_from_letter, has_flags)
        opening_delimiter_regex = re.escape(opening_delimiter)
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=syntax_type_is_block)
        content_regex = build_content_regex(prohibited_content_regex)
        closing_delimiter_regex = re.escape(closing_delimiter)

        return ''.join([
            block_anchoring_regex,
            flags_regex,
            opening_delimiter_regex,
            attribute_specifications_regex,
            content_regex,
            block_anchoring_regex,
            closing_delimiter_regex
        ])

    def build_substitute_function(
        self,
        flag_name_from_letter,
        has_flags,
        attribute_specifications,
        tag_name,
    ):
        def substitute_function(match):
            enabled_flag_names = \
                ReplacementWithAllowedFlags.get_enabled_flag_names(match, flag_name_from_letter, has_flags)

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    attribute_specifications + ' ' + none_to_empty_string(matched_attribute_specifications)
                attributes_sequence = build_attributes_sequence(combined_attribute_specifications, use_protection=True)
            else:
                attributes_sequence = ''

            content = match.group('content')
            for replacement in self._content_replacements:
                content = replacement.apply(content, enabled_flag_names)

            if tag_name is None:
                substitute = content
            else:
                substitute = f'<{tag_name}{attributes_sequence}>{content}</{tag_name}>'

            for replacement in self._concluding_replacements:
                substitute = replacement.apply(substitute, enabled_flag_names)

            return substitute

        return substitute_function


class ExtensibleFenceReplacement(
    ReplacementWithSyntaxType,
    ReplacementWithAllowedFlags,
    ReplacementWithAttributeSpecifications,
    ReplacementWithProhibitedContent,
    ReplacementWithContentReplacements,
    ReplacementWithTagName,
    ReplacementWithConcludingReplacements,
    Replacement,
):
    """
    A generalised extensible-fence-style replacement rule.

    Inspired by the repeatable backticks of John Gruber's Markdown.
    CMD replacement rule syntax:
    ````
    ExtensibleFenceReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - syntax_type: BLOCK | INLINE (mandatory)
    - allowed_flags: (def) NONE | «letter»=«FLAG_NAME» [...]
    - prologue_delimiter: (def) NONE | «string»
    - extensible_delimiter: «character_repeated» (mandatory)
    - attribute_specifications: (def) NONE | EMPTY | «string»
    - prohibited_content: (def) NONE | BLOCKS | ANCHORED_BLOCKS
    - content_replacements: (def) NONE | #«id» [...]
    - epilogue_delimiter: (def) NONE | «string»
    - tag_name: (def) NONE | «name»
    - concluding_replacements: (def) NONE | #«id» [...]
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._prologue_delimiter = ''
        self._extensible_delimiter_character = None
        self._extensible_delimiter_min_length = None
        self._epilogue_delimiter = ''
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'syntax_type',
            'allowed_flags',
            'prologue_delimiter',
            'extensible_delimiter',
            'attribute_specifications',
            'prohibited_content',
            'content_replacements',
            'epilogue_delimiter',
            'tag_name',
            'concluding_replacements',
        )

    @property
    def prologue_delimiter(self):
        return self._prologue_delimiter

    @prologue_delimiter.setter
    def prologue_delimiter(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `prologue_delimiter` after `commit()`')

        self._prologue_delimiter = value

    @property
    def extensible_delimiter_character(self):
        return self._extensible_delimiter_character

    @extensible_delimiter_character.setter
    def extensible_delimiter_character(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `extensible_delimiter_character` after `commit()`')

        self._extensible_delimiter_character = value

    @property
    def extensible_delimiter_min_length(self):
        return self._extensible_delimiter_min_length

    @extensible_delimiter_min_length.setter
    def extensible_delimiter_min_length(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `extensible_delimiter_min_length` after `commit()`')

        self._extensible_delimiter_min_length = value

    @property
    def epilogue_delimiter(self):
        return self._epilogue_delimiter

    @epilogue_delimiter.setter
    def epilogue_delimiter(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `epilogue_delimiter` after `commit()`')

        self._epilogue_delimiter = value

    def _validate_mandatory_attributes(self):
        if self._syntax_type_is_block is None:
            raise MissingAttributeException('syntax_type')

        if self._extensible_delimiter_character is None:
            raise MissingAttributeException('extensible_delimiter')

    def _set_apply_method_variables(self):
        self._has_flags = len(self._flag_name_from_letter) > 0
        self._regex_pattern_compiled = \
            re.compile(
                ExtensibleFenceReplacement.build_regex_pattern(
                    self._syntax_type_is_block,
                    self._flag_name_from_letter,
                    self._has_flags,
                    self._prologue_delimiter,
                    self._extensible_delimiter_character,
                    self._extensible_delimiter_min_length,
                    self._attribute_specifications,
                    self._prohibited_content_regex,
                    self._epilogue_delimiter,
                ),
                flags=re.ASCII | re.MULTILINE | re.VERBOSE,
            )
        self._substitute_function = \
            self.build_substitute_function(
                self._flag_name_from_letter,
                self._has_flags,
                self._attribute_specifications,
                self._tag_name,
            )

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(
        syntax_type_is_block,
        flag_name_from_letter,
        has_flags,
        prologue_delimiter,
        extensible_delimiter_character,
        extensible_delimiter_min_length,
        attribute_specifications,
        prohibited_content_regex,
        epilogue_delimiter,
    ):
        block_anchoring_regex = build_block_anchoring_regex(syntax_type_is_block)
        flags_regex = build_flags_regex(flag_name_from_letter, has_flags)
        prologue_delimiter_regex = re.escape(prologue_delimiter)
        extensible_delimiter_opening_regex = \
            build_extensible_delimiter_opening_regex(extensible_delimiter_character, extensible_delimiter_min_length)
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=syntax_type_is_block)
        content_regex = build_content_regex(prohibited_content_regex)
        extensible_delimiter_closing_regex = build_extensible_delimiter_closing_regex()
        epilogue_delimiter_regex = re.escape(epilogue_delimiter)

        return ''.join([
            block_anchoring_regex,
            flags_regex,
            prologue_delimiter_regex,
            extensible_delimiter_opening_regex,
            attribute_specifications_regex,
            content_regex,
            block_anchoring_regex,
            extensible_delimiter_closing_regex,
            epilogue_delimiter_regex,
        ])

    def build_substitute_function(self, flag_name_from_letter, has_flags, attribute_specifications, tag_name):
        def substitute_function(match):
            enabled_flag_names = \
                ReplacementWithAllowedFlags.get_enabled_flag_names(match, flag_name_from_letter, has_flags)

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    attribute_specifications + ' ' + none_to_empty_string(matched_attribute_specifications)
                attributes_sequence = \
                    build_attributes_sequence(combined_attribute_specifications, use_protection=True)
            else:
                attributes_sequence = ''

            content = match.group('content')
            for replacement in self._content_replacements:
                content = replacement.apply(content, enabled_flag_names)

            if tag_name is None:
                substitute = content
            else:
                substitute = f'<{tag_name}{attributes_sequence}>{content}</{tag_name}>'

            for replacement in self._concluding_replacements:
                substitute = replacement.apply(substitute, enabled_flag_names)

            return substitute

        return substitute_function


class PartitioningReplacement(
    ReplacementWithAttributeSpecifications,
    ReplacementWithContentReplacements,
    ReplacementWithTagName,
    ReplacementWithConcludingReplacements,
    Replacement,
):
    """
    A generalised partitioning replacement rule.

    Does partitioning by consuming everything from a
    starting pattern up to but not including an ending pattern.
    CMD replacement rule syntax:
    ````
    PartitioningReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - starting_pattern: «regex» (mandatory)
    - attribute_specifications: (def) NONE | EMPTY | «string»
    - content_replacements: (def) NONE | #«id» [...]
    - ending_pattern: (def) NONE | «regex»
    - tag_name: (def) NONE | «name»
    - concluding_replacements: (def) NONE | #«id» [...]
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._starting_pattern = None
        self._ending_pattern = None
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'starting_pattern',
            'attribute_specifications',
            'content_replacements',
            'ending_pattern',
            'tag_name',
            'concluding_replacements',
        )

    @property
    def starting_pattern(self):
        return self._starting_pattern

    @starting_pattern.setter
    def starting_pattern(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `starting_pattern` after `commit()`')

        self._starting_pattern = value

    @property
    def ending_pattern(self):
        return self._ending_pattern

    @ending_pattern.setter
    def ending_pattern(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot set `ending_pattern` after `commit()`')

        self._ending_pattern = value

    def _validate_mandatory_attributes(self):
        if self._starting_pattern is None:
            raise MissingAttributeException('starting_pattern')

    def _set_apply_method_variables(self):
        self._regex_pattern_compiled = \
            re.compile(
                PartitioningReplacement.build_regex_pattern(
                    self._starting_pattern,
                    self._attribute_specifications,
                    self._ending_pattern,
                ),
                flags=re.ASCII | re.MULTILINE | re.VERBOSE,
            )
        self._substitute_function = \
            self.build_substitute_function(
                self._attribute_specifications,
                self._tag_name,
            )

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(starting_pattern, attribute_specifications, ending_pattern):
        anchoring_regex = build_block_anchoring_regex(syntax_type_is_block=True)
        starting_regex = f'(?: {starting_pattern} )'
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=False, allow_omission=False)
        if attribute_specifications_regex == '':
            attribute_specifications_or_whitespace_regex = r'[\s]+?'
        else:
            attribute_specifications_or_whitespace_regex = \
                fr'(?: {attribute_specifications_regex} | [\s]+? )'
        content_regex = build_content_regex()
        attribute_specifications_no_capture_regex = \
            build_attribute_specifications_regex(
                attribute_specifications,
                require_newline=False,
                capture_attribute_specifications=False,
                allow_omission=False,
            )
        if attribute_specifications_no_capture_regex == '':
            attribute_specifications_no_capture_or_whitespace_regex = r'[\s]+'
        else:
            attribute_specifications_no_capture_or_whitespace_regex = \
                fr'(?: {attribute_specifications_no_capture_regex} | [\s]+ )'
        if ending_pattern is None:
            ending_lookahead_regex = r'(?= \Z )'
        else:
            ending_regex = f'(?: {ending_pattern} )'
            ending_lookahead_regex = \
                (
                    '(?= '
                    + anchoring_regex
                    + ending_regex
                    + attribute_specifications_no_capture_or_whitespace_regex
                    + r' | \Z )'
                )

        return ''.join([
            anchoring_regex,
            starting_regex,
            attribute_specifications_or_whitespace_regex,
            content_regex,
            ending_lookahead_regex,
        ])

    def build_substitute_function(self, attribute_specifications, tag_name):
        def substitute_function(match):
            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    attribute_specifications + ' ' + none_to_empty_string(matched_attribute_specifications)
                attributes_sequence = build_attributes_sequence(combined_attribute_specifications, use_protection=True)
            else:
                attributes_sequence = ''

            content = match.group('content')
            for replacement in self._content_replacements:
                content = replacement.apply(content)

            if tag_name is None:
                substitute = content
            else:
                substitute = f'<{tag_name}{attributes_sequence}>{content}</{tag_name}>\n'

            for replacement in self._concluding_replacements:
                substitute = replacement.apply(substitute)

            return substitute

        return substitute_function


class InlineAssortedDelimitersReplacement(
    ReplacementWithAttributeSpecifications,
    ReplacementWithProhibitedContent,
    Replacement
):
    """
    An inline assorted-delimiters replacement rule.

    CMD replacement rule syntax:
    ````
    InlineAssortedDelimitersReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - delimiter_conversion:
      «character» | «character_doubled»=«tag_name» [...] (mandatory)
    - attribute_specifications: (def) NONE | EMPTY | «string»
    - prohibited_content: (def) NONE | BLOCKS | ANCHORED_BLOCKS
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._tag_name_from_delimiter_length_from_character = {}
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'delimiter_conversion',
            'attribute_specifications',
            'prohibited_content',
        )

    @property
    def tag_name_from_delimiter_length_from_character(self):
        return self._tag_name_from_delimiter_length_from_character

    @tag_name_from_delimiter_length_from_character.setter
    def tag_name_from_delimiter_length_from_character(self, value):
        if self._is_committed:
            raise CommittedMutateException('error: cannot call `add_delimiter_conversion(...)` after `commit()`')

        self._tag_name_from_delimiter_length_from_character = copy.deepcopy(value)

    def _validate_mandatory_attributes(self):
        if len(self._tag_name_from_delimiter_length_from_character) == 0:
            raise MissingAttributeException('delimiter_conversion')

    def _set_apply_method_variables(self):
        self._regex_pattern_compiled = \
            re.compile(
                InlineAssortedDelimitersReplacement.build_regex_pattern(
                    self._tag_name_from_delimiter_length_from_character,
                    self._attribute_specifications,
                    self._prohibited_content_regex,
                ),
                flags=re.ASCII | re.MULTILINE | re.VERBOSE,
            )
        self._substitute_function = \
            self.build_substitute_function(
                self._tag_name_from_delimiter_length_from_character,
                self._attribute_specifications,
            )

    def _apply(self, string):
        string_has_changed = True

        while string_has_changed:
            new_string = re.sub(self._regex_pattern_compiled, self._substitute_function, string)
            string_has_changed = new_string != string
            string = new_string

        return string

    def build_substitute_function(self, tag_name_from_delimiter_length_from_character, attribute_specifications):
        def substitute_function(match):
            character = match.group('delimiter_character')
            delimiter = match.group('delimiter')
            length = len(delimiter)
            tag_name = tag_name_from_delimiter_length_from_character[character][length]

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    attribute_specifications + ' ' + none_to_empty_string(matched_attribute_specifications)
                attributes_sequence = \
                    build_attributes_sequence(combined_attribute_specifications, use_protection=True)
            else:
                attributes_sequence = ''

            content = match.group('content')
            content = self.apply(content)

            substitute = f'<{tag_name}{attributes_sequence}>{content}</{tag_name}>'

            return substitute

        return substitute_function

    @staticmethod
    def build_regex_pattern(
        tag_name_from_delimiter_length_from_character,
        attribute_specifications,
        prohibited_content_regex,
    ):
        optional_pipe_regex = '[|]?'

        single_characters = set()
        either_characters = set()
        double_characters = set()
        all_characters = set()
        for character in tag_name_from_delimiter_length_from_character:
            tag_name_from_delimiter_length = tag_name_from_delimiter_length_from_character[character]
            delimiter_lengths = tag_name_from_delimiter_length.keys()
            if delimiter_lengths == {1}:
                single_characters.add(character)
                all_characters.add(character)
            elif delimiter_lengths == {1, 2}:
                either_characters.add(character)
                all_characters.add(character)
            elif delimiter_lengths == {2}:
                double_characters.add(character)
                all_characters.add(character)

        single_class_regex = build_captured_character_class_regex(single_characters, 'single')
        either_class_regex = build_captured_character_class_regex(either_characters, 'either')
        double_class_regex = build_captured_character_class_regex(double_characters, 'double')
        class_regexes = [double_class_regex, either_class_regex, single_class_regex]
        delimiter_character_alternatives = \
            ' | '.join(
                class_regex
                for class_regex in class_regexes
                if class_regex is not None
            )
        delimiter_character_regex = f'(?P<delimiter_character> {delimiter_character_alternatives} )'

        if either_class_regex is not None:
            if double_class_regex is not None:
                repetition_regex = '(?(double) (?P=double) | (?(either) (?P=either)? ) )'
            else:
                repetition_regex = '(?(either) (?P=either)? )'
        else:
            if double_class_regex is not None:
                repetition_regex = '(?(double) (?P=double) )'
            else:
                repetition_regex = ''
        opening_delimiter_regex = f'(?P<delimiter> {delimiter_character_regex} {repetition_regex} )'

        after_opening_delimiter_regex = r'(?! [\s] | [<][/] )'
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=False)
        before_content_whitespace_regex = r'[\s]*'

        if prohibited_content_regex is None:
            prohibited_content_regex = '(?P=delimiter_character)'
        else:
            prohibited_content_regex = f'(?P=delimiter_character) | {prohibited_content_regex}'
        content_regex = build_content_regex(prohibited_content_regex, permit_empty=False)

        before_closing_delimiter_regex = r'(?<! [\s] | [|] )'
        closing_delimiter_regex = '(?P=delimiter)'

        return ''.join([
            optional_pipe_regex,
            opening_delimiter_regex,
            after_opening_delimiter_regex,
            attribute_specifications_regex,
            before_content_whitespace_regex,
            content_regex,
            before_closing_delimiter_regex,
            closing_delimiter_regex,
        ])


class HeadingReplacement(
    ReplacementWithAttributeSpecifications,
    Replacement,
):
    """
    A replacement rule for headings.

    CMD replacement rule syntax:
    ````
    HeadingReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - attribute_specifications: (def) NONE | EMPTY | «string»
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'attribute_specifications',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        self._regex_pattern_compiled = \
            re.compile(
                HeadingReplacement.build_regex_pattern(self._attribute_specifications),
                flags=re.ASCII | re.MULTILINE | re.VERBOSE,
            )
        self._substitute_function = HeadingReplacement.build_substitute_function(self._attribute_specifications)

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(attribute_specifications):

        block_anchoring_regex = \
            build_block_anchoring_regex(syntax_type_is_block=True, capture_anchoring_whitespace=True)
        opening_hashes_regex = '(?P<opening_hashes> [#]{1,6} )'
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=False)
        content_starter_regex = r'(?: [^\S\n]+ (?P<content_starter> [^\n]*? ) )? [^\S\n]*'
        content_continuation_regex = \
            r'(?P<content_continuation> (?: \n (?P=anchoring_whitespace) [^\S\n]+ [^\n]* )* )'
        closing_hashes_regex = '[#]*'
        trailing_horizontal_whitespace_regex = r'[^\S\n]* $'

        return ''.join([
            block_anchoring_regex,
            opening_hashes_regex,
            attribute_specifications_regex,
            content_starter_regex,
            content_continuation_regex,
            closing_hashes_regex,
            trailing_horizontal_whitespace_regex,
        ])

    @staticmethod
    def build_substitute_function(attribute_specifications):
        def substitute_function(match):
            opening_hashes = match.group('opening_hashes')
            heading_level = len(opening_hashes)
            tag_name = f'h{heading_level}'

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    attribute_specifications + ' ' + none_to_empty_string(matched_attribute_specifications)
                attributes_sequence = build_attributes_sequence(combined_attribute_specifications, use_protection=True)
            else:
                attributes_sequence = ''

            content_starter = match.group('content_starter')
            content_continuation = match.group('content_continuation')
            content = none_to_empty_string(content_starter) + content_continuation

            substitute = f'<{tag_name}{attributes_sequence}>{content}</{tag_name}>'

            return substitute

        return substitute_function


class ReferenceDefinitionReplacement(
    ReplacementWithAttributeSpecifications,
    Replacement,
):
    """
    A replacement rule for consuming reference definitions.

    CMD replacement rule syntax:
    ````
    ReferenceDefinitionReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - attribute_specifications: (def) NONE | EMPTY | «string»
    ````
    """
    def __init__(self, id_, reference_master, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._reference_master = reference_master
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'attribute_specifications',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        self._regex_pattern_compiled = \
            re.compile(
                ReferenceDefinitionReplacement.build_regex_pattern(self._attribute_specifications),
                flags=re.ASCII | re.MULTILINE | re.VERBOSE,
            )
        self._substitute_function = self.build_substitute_function(self._attribute_specifications)

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(attribute_specifications):
        block_anchoring_regex = \
            build_block_anchoring_regex(syntax_type_is_block=True, capture_anchoring_whitespace=True)
        label_regex = r'\[ [\s]* (?P<label> [^\]]*? ) [\s]* \]'
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=False)
        colon_regex = '[:]'
        maybe_hanging_whitespace_regex = build_maybe_hanging_whitespace_regex()
        uri_regex = build_uri_regex(be_greedy=True)
        whitespace_then_uri_regex = f'{maybe_hanging_whitespace_regex}{uri_regex}'
        title_regex = build_title_regex()
        whitespace_then_title_regex = f'(?: {maybe_hanging_whitespace_regex}{title_regex} )?'
        trailing_horizontal_whitespace_regex = r'[^\S\n]* $'

        return ''.join(
            [
                block_anchoring_regex,
                label_regex,
                attribute_specifications_regex,
                colon_regex,
                whitespace_then_uri_regex,
                whitespace_then_title_regex,
                trailing_horizontal_whitespace_regex,
            ]
        )

    def build_substitute_function(self, attribute_specifications):
        def substitute_function(match):
            label = match.group('label')

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    attribute_specifications + ' ' + none_to_empty_string(matched_attribute_specifications)
            else:
                combined_attribute_specifications = ''

            angle_bracketed_uri = match.group('angle_bracketed_uri')
            if angle_bracketed_uri is not None:
                uri = angle_bracketed_uri
            else:
                uri = match.group('bare_uri')

            double_quoted_title = match.group('double_quoted_title')
            if double_quoted_title is not None:
                title = double_quoted_title
            else:
                title = match.group('single_quoted_title')

            self._reference_master.store_definition(label, combined_attribute_specifications, uri, title)

            return ''

        return substitute_function


class SpecifiedImageReplacement(
    ReplacementWithAttributeSpecifications,
    ReplacementWithProhibitedContent,
    Replacement,
):
    """
    A replacement rule for specified images.

    CMD replacement rule syntax:
    ````
    SpecifiedImageReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - attribute_specifications: (def) NONE | EMPTY | «string»
    - prohibited_content: (def) NONE | BLOCKS | ANCHORED_BLOCKS
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'attribute_specifications',
            'prohibited_content',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        self._regex_pattern_compiled = \
            re.compile(
                SpecifiedImageReplacement.build_regex_pattern(
                    self._attribute_specifications,
                    self._prohibited_content_regex,
                ),
                flags=re.ASCII | re.VERBOSE,
            )
        self._substitute_function = SpecifiedImageReplacement.build_substitute_function(self._attribute_specifications)

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(attribute_specifications, prohibited_content_regex):
        exclamation_mark_regex = '[!]'
        alt_text_regex = \
            build_content_regex(
                prohibited_content_regex,
                permitted_content_regex=r'[^\]]',
                capture_group_name='alt_text',
            )
        bracketed_alt_text_regex = fr'\[ [\s]* {alt_text_regex} [\s]* \]'
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=False)
        opening_parenthesis_regex = r'\('
        uri_regex = build_uri_regex(be_greedy=False)
        whitespace_then_uri_regex = fr'(?: [\s]* {uri_regex} )?'
        title_regex = build_title_regex()
        whitespace_then_title_regex = fr'(?: [\s]* {title_regex} )?'
        whitespace_regex = r'[\s]*'
        closing_parenthesis_regex = r'\)'

        return ''.join([
            exclamation_mark_regex,
            bracketed_alt_text_regex,
            attribute_specifications_regex,
            opening_parenthesis_regex,
            whitespace_then_uri_regex,
            whitespace_then_title_regex,
            whitespace_regex,
            closing_parenthesis_regex,
        ])

    @staticmethod
    def build_substitute_function(attribute_specifications):
        def substitute_function(match):
            alt = match.group('alt_text')
            alt_protected = PlaceholderMaster.protect(alt)
            alt_attribute_specification = f'alt={alt_protected}'

            angle_bracketed_uri = match.group('angle_bracketed_uri')
            if angle_bracketed_uri is not None:
                src = angle_bracketed_uri
            else:
                src = match.group('bare_uri')

            src_protected = PlaceholderMaster.protect(none_to_empty_string(src))
            src_attribute_specification = f'src={src_protected}'

            double_quoted_title = match.group('double_quoted_title')
            if double_quoted_title is not None:
                title = double_quoted_title
            else:
                title = match.group('single_quoted_title')

            if title is not None:
                title_protected = PlaceholderMaster.protect(title)
                title_attribute_specification = f'title={title_protected}'
            else:
                title_attribute_specification = ''

            alt_src_title_attribute_specifications = \
                ' '.join([
                    alt_attribute_specification,
                    src_attribute_specification,
                    title_attribute_specification,
                ])

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    ' '.join([
                        alt_src_title_attribute_specifications,
                        attribute_specifications,
                        none_to_empty_string(matched_attribute_specifications),
                    ])
            else:
                combined_attribute_specifications = alt_src_title_attribute_specifications

            attributes_sequence = build_attributes_sequence(combined_attribute_specifications, use_protection=True)

            substitute = f'<img{attributes_sequence}>'

            return substitute

        return substitute_function


class ReferencedImageReplacement(
    ReplacementWithAttributeSpecifications,
    ReplacementWithProhibitedContent,
    Replacement,
):
    """
    A replacement rule for referenced images.

    CMD replacement rule syntax:
    ````
    ReferencedImageReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - attribute_specifications: (def) NONE | EMPTY | «string»
    - prohibited_content: (def) NONE | BLOCKS | ANCHORED_BLOCKS
    ````
    """
    def __init__(self, id_, reference_master, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._reference_master = reference_master
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'attribute_specifications',
            'prohibited_content',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        self._regex_pattern_compiled = \
            re.compile(
                ReferencedImageReplacement.build_regex_pattern(
                    self._attribute_specifications,
                    self._prohibited_content_regex,
                ),
                flags=re.ASCII | re.VERBOSE,
            )
        self._substitute_function = self.build_substitute_function(self._attribute_specifications)

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(attribute_specifications, prohibited_content_regex):

        exclamation_mark_regex = '[!]'
        alt_text_regex = \
            build_content_regex(
                prohibited_content_regex,
                permitted_content_regex=r'[^\]]',
                capture_group_name='alt_text',
            )
        bracketed_alt_text_regex = fr'\[ [\s]* {alt_text_regex} [\s]* \]'
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=False)
        label_regex = \
            build_content_regex(
                prohibited_content_regex,
                permitted_content_regex=r'[^\]]',
                capture_group_name='label',
            )
        bracketed_label_regex = fr'(?: \[ [\s]* {label_regex} [\s]* \] )?'

        return ''.join([
            exclamation_mark_regex,
            bracketed_alt_text_regex,
            attribute_specifications_regex,
            bracketed_label_regex,
        ])

    def build_substitute_function(self, attribute_specifications):
        def substitute_function(match):
            alt = match.group('alt_text')
            alt_protected = PlaceholderMaster.protect(alt)
            alt_attribute_specification = f'alt={alt_protected}'

            label = match.group('label')
            if label is None or label == '':
                label = alt

            try:
                referenced_attribute_specifications, src, title = self._reference_master.load_definition(label)
            except UnrecognisedLabelException:
                return match.group()

            src_protected = PlaceholderMaster.protect(none_to_empty_string(src))
            src_attribute_specification = f'src={src_protected}'

            if title is not None:
                title_protected = PlaceholderMaster.protect(title)
                title_attribute_specification = f'title={title_protected}'
            else:
                title_attribute_specification = ''

            alt_src_title_referenced_attribute_specifications = \
                ' '.join([
                    alt_attribute_specification,
                    src_attribute_specification,
                    title_attribute_specification,
                    none_to_empty_string(referenced_attribute_specifications)
                ])

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    ' '.join([
                        alt_src_title_referenced_attribute_specifications,
                        attribute_specifications,
                        none_to_empty_string(matched_attribute_specifications),
                    ])
            else:
                combined_attribute_specifications = alt_src_title_referenced_attribute_specifications

            attributes_sequence = build_attributes_sequence(combined_attribute_specifications, use_protection=True)

            substitute = f'<img{attributes_sequence}>'

            return substitute

        return substitute_function


class ExplicitLinkReplacement(
    ReplacementWithAllowedFlags,
    ReplacementWithAttributeSpecifications,
    ReplacementWithContentReplacements,
    ReplacementWithConcludingReplacements,
    Replacement,
):
    """
    A replacement rule for explicit links.

    CMD replacement rule syntax:
    ````
    ExplicitLinkReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - allowed_flags: (def) NONE | «letter»=«FLAG_NAME» [...]
    - attribute_specifications: (def) NONE | EMPTY | «string»
    - content_replacements: (def) NONE | #«id» [...]
    - concluding_replacements: (def) NONE | #«id» [...]
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'allowed_flags',
            'attribute_specifications',
            'content_replacements',
            'concluding_replacements',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):

        self._has_flags = len(self._flag_name_from_letter) > 0
        self._regex_pattern_compiled = \
            re.compile(
                ExplicitLinkReplacement.build_regex_pattern(
                    self._flag_name_from_letter,
                    self._has_flags,
                    self._attribute_specifications,
                ),
                flags=re.ASCII | re.VERBOSE,
            )
        self._substitute_function = \
            self.build_substitute_function(
                self._flag_name_from_letter,
                self._has_flags,
                self._attribute_specifications,
            )

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(flag_name_from_letter, has_flags, attribute_specifications):
        flags_regex = build_flags_regex(flag_name_from_letter, has_flags)
        opening_angle_bracket_regex = '[<]'
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=False)
        uri_regex = r'(?P<uri> [a-zA-Z.+-]+ [:] [^\s>]*? )'
        closing_angle_bracket_regex = '[>]'

        return ''.join([
            flags_regex,
            opening_angle_bracket_regex,
            attribute_specifications_regex,
            uri_regex,
            closing_angle_bracket_regex,
        ])

    def build_substitute_function(self, flag_name_from_letter, has_flags, attribute_specifications):
        def substitute_function(match):
            enabled_flag_names = \
                ReplacementWithAllowedFlags.get_enabled_flag_names(match, flag_name_from_letter, has_flags)

            href = match.group('uri')
            href_protected = PlaceholderMaster.protect(href)
            href_attribute_specification = f'href={href_protected}'

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    ' '.join([
                        href_attribute_specification,
                        none_to_empty_string(matched_attribute_specifications),
                    ])
            else:
                combined_attribute_specifications = href_attribute_specification

            attributes_sequence = build_attributes_sequence(combined_attribute_specifications, use_protection=True)

            content = href
            for replacement in self._content_replacements:
                content = replacement.apply(content, enabled_flag_names)

            substitute = f'<a{attributes_sequence}>{content}</a>'
            for replacement in self._concluding_replacements:
                substitute = replacement.apply(substitute, enabled_flag_names)

            return substitute

        return substitute_function


class SpecifiedLinkReplacement(
    ReplacementWithAttributeSpecifications,
    ReplacementWithProhibitedContent,
    Replacement,
):
    """
    A replacement rule for specified links.

    CMD replacement rule syntax:
    ````
    SpecifiedLinkReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - attribute_specifications: (def) NONE | EMPTY | «string»
    - prohibited_content: (def) NONE | BLOCKS | ANCHORED_BLOCKS
    ````
    """
    def __init__(self, id_, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'attribute_specifications',
            'prohibited_content',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        self._regex_pattern_compiled = \
            re.compile(
                SpecifiedLinkReplacement.build_regex_pattern(
                    self._attribute_specifications,
                    self._prohibited_content_regex,
                ),
                flags=re.ASCII | re.VERBOSE,
            )
        self._substitute_function = SpecifiedLinkReplacement.build_substitute_function(self._attribute_specifications)

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(attribute_specifications, prohibited_content_regex):
        link_text_regex = \
            build_content_regex(
                prohibited_content_regex,
                permitted_content_regex=r'[^\]]',
                capture_group_name='link_text',
            )
        bracketed_link_text_regex = fr'\[ [\s]* {link_text_regex} [\s]* \]'
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=False)
        opening_parenthesis_regex = r'\('
        uri_regex = build_uri_regex(be_greedy=False)
        whitespace_then_uri_regex = fr'(?: [\s]* {uri_regex} )?'
        title_regex = build_title_regex()
        whitespace_then_title_regex = fr'(?: [\s]* {title_regex} )?'
        whitespace_regex = r'[\s]*'
        closing_parenthesis_regex = r'\)'

        return ''.join([
            bracketed_link_text_regex,
            attribute_specifications_regex,
            opening_parenthesis_regex,
            whitespace_then_uri_regex,
            whitespace_then_title_regex,
            whitespace_regex,
            closing_parenthesis_regex,
        ])

    @staticmethod
    def build_substitute_function(attribute_specifications):
        def substitute_function(match):
            content = match.group('link_text')

            angle_bracketed_uri = match.group('angle_bracketed_uri')
            if angle_bracketed_uri is not None:
                href = angle_bracketed_uri
            else:
                href = match.group('bare_uri')

            if href is not None:
                href_protected = PlaceholderMaster.protect(href)
                href_attribute_specification = f'href={href_protected}'
            else:
                href_attribute_specification = ''

            double_quoted_title = match.group('double_quoted_title')
            if double_quoted_title is not None:
                title = double_quoted_title
            else:
                title = match.group('single_quoted_title')

            if title is not None:
                title_protected = PlaceholderMaster.protect(title)
                title_attribute_specification = f'title={title_protected}'
            else:
                title_attribute_specification = ''

            href_title_attribute_specifications = \
                ' '.join([
                    href_attribute_specification,
                    title_attribute_specification,
                ])

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    ' '.join([
                        href_title_attribute_specifications,
                        attribute_specifications,
                        none_to_empty_string(matched_attribute_specifications),
                    ])
            else:
                combined_attribute_specifications = href_title_attribute_specifications

            attributes_sequence = build_attributes_sequence(combined_attribute_specifications, use_protection=True)

            substitute = f'<a{attributes_sequence}>{content}</a>'

            return substitute

        return substitute_function


class ReferencedLinkReplacement(
    ReplacementWithAttributeSpecifications,
    ReplacementWithProhibitedContent,
    Replacement,
):
    """
    A replacement rule for referenced links.

    CMD replacement rule syntax:
    ````
    ReferencedLinkReplacement: #«id»
    - queue_position: (def) NONE | ROOT | BEFORE #«id» | AFTER #«id»
    - attribute_specifications: (def) NONE | EMPTY | «string»
    - prohibited_content: (def) NONE | BLOCKS | ANCHORED_BLOCKS
    ````
    """
    def __init__(self, id_, reference_master, verbose_mode_enabled):
        super().__init__(id_, verbose_mode_enabled)
        self._reference_master = reference_master
        self._regex_pattern_compiled = None
        self._substitute_function = None

    def attribute_names(self):
        return (
            'queue_position',
            'attribute_specifications',
            'prohibited_content',
        )

    def _validate_mandatory_attributes(self):
        pass

    def _set_apply_method_variables(self):
        self._regex_pattern_compiled = \
            re.compile(
                ReferencedLinkReplacement.build_regex_pattern(
                    self._attribute_specifications,
                    self._prohibited_content_regex,
                ),
                flags=re.ASCII | re.VERBOSE,
            )
        self._substitute_function = self.build_substitute_function(self._attribute_specifications)

    def _apply(self, string):
        return re.sub(
            self._regex_pattern_compiled,
            self._substitute_function,
            string,
        )

    @staticmethod
    def build_regex_pattern(attribute_specifications, prohibited_content_regex):
        link_text_regex = \
            build_content_regex(
                prohibited_content_regex,
                permitted_content_regex=r'[^\]]',
                capture_group_name='link_text',
            )
        bracketed_link_text_regex = fr'\[ [\s]* {link_text_regex} [\s]* \]'
        attribute_specifications_regex = \
            build_attribute_specifications_regex(attribute_specifications, require_newline=False)
        label_regex = \
            build_content_regex(
                prohibited_content_regex,
                permitted_content_regex=r'[^\]]',
                capture_group_name='label',
            )
        bracketed_label_regex = fr'(?: \[ [\s]* {label_regex} [\s]* \] )?'

        return ''.join([
            bracketed_link_text_regex,
            attribute_specifications_regex,
            bracketed_label_regex,
        ])

    def build_substitute_function(self, attribute_specifications):
        def substitute_function(match):
            content = match.group('link_text')

            label = match.group('label')
            if label is None or label == '':
                label = content

            try:
                referenced_attribute_specifications, href, title = self._reference_master.load_definition(label)
            except UnrecognisedLabelException:
                return match.group()

            if href is not None:
                href_protected = PlaceholderMaster.protect(href)
                href_attribute_specification = f'href={href_protected}'
            else:
                href_attribute_specification = ''

            if title is not None:
                title_protected = PlaceholderMaster.protect(title)
                title_attribute_specification = f'title={title_protected}'
            else:
                title_attribute_specification = ''

            href_title_referenced_attribute_specifications = \
                ' '.join([
                    href_attribute_specification,
                    title_attribute_specification,
                    none_to_empty_string(referenced_attribute_specifications)
                ])

            if attribute_specifications is not None:
                matched_attribute_specifications = match.group('attribute_specifications')
                combined_attribute_specifications = \
                    ' '.join([
                        href_title_referenced_attribute_specifications,
                        attribute_specifications,
                        none_to_empty_string(matched_attribute_specifications),
                    ])
            else:
                combined_attribute_specifications = href_title_referenced_attribute_specifications

            attributes_sequence = build_attributes_sequence(combined_attribute_specifications, use_protection=True)

            substitute = f'<a{attributes_sequence}>{content}</a>'

            return substitute

        return substitute_function


CMD_REPLACEMENT_SYNTAX_HELP = '''\
In CMD replacement rule syntax, a line must be one of the following:
(1) whitespace-only;
(2) a comment (beginning with `#`);
(3) a rules inclusion (`< «included_file_name»`);
(4) a class declaration (`«ClassName»: #«id»`);
(5) the start of an attribute declaration (`- «name»: «value»`);
(6) the start of a substitution declaration (`* «pattern» --> «substitute»`);
(7) a continuation (beginning with whitespace).
- Note for (3): if «included_file_name» begins with a slash,
  it is parsed relative to the working directory;
  otherwise it is parsed relative to the current file.
- Note for (6): the number of hyphens in the delimiter `-->`
  may be arbitrarily increased should «pattern» contain
  a run of hyphens followed by a closing angle-bracket.
- Note for (7): continuations are only allowed for attribute declarations
  and for substitution declarations.
'''


class ReplacementMaster:
    """
    Object governing the parsing and application of replacement rules.

    ## `legislate`

    Parses CMD replacement rule syntax.
    See the constant `CMD_REPLACEMENT_SYNTAX_HELP` above.

    Terminology:
    - Class declarations are _committed_.
    - Attribute and substitution declarations are _staged_.

    ## `execute`

    Applies the legislated replacements.
    """
    def __init__(self, cmd_file_name, verbose_mode_enabled):
        self._opened_file_names = [cmd_file_name]
        self._replacement_from_id = {}
        self._root_replacement_id = None
        self._replacement_queue = []
        self._reference_master = ReferenceMaster()
        self._verbose_mode_enabled = verbose_mode_enabled

    @staticmethod
    def print_error(message, rules_file_name, start_line_number, end_line_number=None):
        source_file = f'`{rules_file_name}`'

        if end_line_number is None or start_line_number == end_line_number - 1:
            line_number_range = f'line {start_line_number}'
        else:
            line_number_range = f'lines {start_line_number} to {end_line_number - 1}'

        print(f'error: {source_file}, {line_number_range}: {message}', file=sys.stderr)

    @staticmethod
    def print_traceback(exception):
        traceback.print_exception(type(exception), exception, exception.__traceback__)

    @staticmethod
    def is_whitespace_only(line):
        return re.fullmatch(r'[\s]*', line, flags=re.ASCII)

    @staticmethod
    def is_comment(line):
        return line.startswith('#')

    @staticmethod
    def compute_rules_inclusion_match(line):
        return re.fullmatch(
            r'''
                [<][ ]
                    (?:
                        [/] (?P<included_file_name> [\S][\s\S]*? )
                            |
                        (?P<included_file_name_relative> [\S][\s\S]*? )
                    )
                [\s]*
            ''',
            line,
            flags=re.ASCII | re.VERBOSE,
        )

    def process_rules_inclusion_line(self, rules_inclusion_match, rules_file_name, cmd_name, line_number):
        included_file_name_relative = rules_inclusion_match.group('included_file_name_relative')
        if included_file_name_relative is not None:
            included_file_name = os.path.join(os.path.dirname(rules_file_name), included_file_name_relative)
        else:
            included_file_name = rules_inclusion_match.group('included_file_name')
        included_file_name = os.path.normpath(included_file_name)

        try:
            with open(included_file_name, 'r', encoding='utf-8') as included_file:
                for opened_file_name in self._opened_file_names:
                    if os.path.samefile(opened_file_name, included_file_name):
                        self._opened_file_names.append(included_file_name)
                        recursive_inclusion_string = \
                            ' includes '.join(
                                f'`{opened_file_name}`'
                                for opened_file_name in self._opened_file_names
                            )
                        ReplacementMaster.print_error(
                            f'recursive inclusion: {recursive_inclusion_string}',
                            rules_file_name,
                            line_number,
                        )
                        sys.exit(GENERIC_ERROR_EXIT_CODE)
                self._opened_file_names.append(included_file_name)
                replacement_rules = included_file.read()
        except FileNotFoundError:
            ReplacementMaster.print_error(
                f'file `{included_file_name}` (relative to terminal) not found',
                rules_file_name,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        self.legislate(replacement_rules, rules_file_name=included_file_name, cmd_name=cmd_name)

    @staticmethod
    def compute_class_declaration_match(line):
        return re.fullmatch(
            r'''
                (?P<class_name> [A-Za-z]+ ) [:]
                [\s]+
                [#] (?P<id_> [a-z0-9-.]+ )
            ''',
            line,
            flags=re.ASCII | re.VERBOSE,
        )

    def process_class_declaration_line(self, class_declaration_match, rules_file_name, line_number):
        class_name = class_declaration_match.group('class_name')
        id_ = class_declaration_match.group('id_')

        if class_name == 'ReplacementSequence':
            replacement = ReplacementSequence(id_, self._verbose_mode_enabled)
        elif class_name == 'PlaceholderMarkerReplacement':
            replacement = PlaceholderMarkerReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'PlaceholderProtectionReplacement':
            replacement = PlaceholderProtectionReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'PlaceholderUnprotectionReplacement':
            replacement = PlaceholderUnprotectionReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'DeIndentationReplacement':
            replacement = DeIndentationReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'OrdinaryDictionaryReplacement':
            replacement = OrdinaryDictionaryReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'RegexDictionaryReplacement':
            replacement = RegexDictionaryReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'FixedDelimitersReplacement':
            replacement = FixedDelimitersReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'ExtensibleFenceReplacement':
            replacement = ExtensibleFenceReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'PartitioningReplacement':
            replacement = PartitioningReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'InlineAssortedDelimitersReplacement':
            replacement = InlineAssortedDelimitersReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'HeadingReplacement':
            replacement = HeadingReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'ReferenceDefinitionReplacement':
            replacement = ReferenceDefinitionReplacement(id_, self._reference_master, self._verbose_mode_enabled)
        elif class_name == 'SpecifiedImageReplacement':
            replacement = SpecifiedImageReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'ReferencedImageReplacement':
            replacement = ReferencedImageReplacement(id_, self._reference_master, self._verbose_mode_enabled)
        elif class_name == 'ExplicitLinkReplacement':
            replacement = ExplicitLinkReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'SpecifiedLinkReplacement':
            replacement = SpecifiedLinkReplacement(id_, self._verbose_mode_enabled)
        elif class_name == 'ReferencedLinkReplacement':
            replacement = ReferencedLinkReplacement(id_, self._reference_master, self._verbose_mode_enabled)
        else:
            ReplacementMaster.print_error(
                f'unrecognised replacement class `{class_name}`',
                rules_file_name,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if id_ in self._replacement_from_id:
            ReplacementMaster.print_error(
                f'replacement already declared with id `{id_}`',
                rules_file_name,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        line_number_range_start = line_number

        return class_name, replacement, line_number_range_start

    @staticmethod
    def compute_attribute_declaration_match(line):
        return re.fullmatch(
            r'''
                [-][ ] (?P<attribute_name> [a-z_]+ ) [:]
                (?P<partial_attribute_value> [\s\S]* )
            ''',
            line,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def process_attribute_declaration_line(
        attribute_declaration_match,
        class_name,
        replacement,
        attribute_value,
        rules_file_name,
        line_number,
    ):
        if replacement is None:
            ReplacementMaster.print_error(
                f'attribute declaration without an active class declaration',
                rules_file_name,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        attribute_name = attribute_declaration_match.group('attribute_name')
        if attribute_name not in replacement.attribute_names():
            ReplacementMaster.print_error(
                f'unrecognised attribute `{attribute_name}` for `{class_name}`',
                rules_file_name,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        partial_attribute_value = attribute_declaration_match.group('partial_attribute_value')
        attribute_value = none_to_empty_string(attribute_value) + partial_attribute_value

        line_number_range_start = line_number

        return attribute_name, attribute_value, line_number_range_start

    @staticmethod
    def compute_substitution_declaration_match(line):
        return re.fullmatch(
            r'[*][ ] (?P<partial_substitution> [\s\S]* )',
            line,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def process_substitution_declaration_line(
        replacement,
        substitution_declaration_match,
        substitution,
        rules_file_name,
        line_number,
    ):
        if replacement is None:
            ReplacementMaster.print_error(
                f'substitution declaration without an active class declaration',
                rules_file_name,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        partial_substitution = substitution_declaration_match.group('partial_substitution')
        substitution = none_to_empty_string(substitution) + partial_substitution

        line_number_range_start = line_number

        return substitution, line_number_range_start

    @staticmethod
    def compute_continuation_match(line):
        return re.fullmatch(
            r'(?P<continuation> [\s]+ [\S][\s\S]* )',
            line,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def process_continuation_line(
        continuation_match,
        attribute_name,
        attribute_value,
        substitution,
        rules_file_name,
        line_number,
    ):
        continuation = continuation_match.group('continuation')

        if attribute_name is not None:
            attribute_value = none_to_empty_string(attribute_value) + '\n' + continuation
        elif substitution is not None:
            substitution = substitution + '\n' + continuation
        else:
            ReplacementMaster.print_error(
                'continuation only allowed for attribute or substitution declarations',
                rules_file_name,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        return attribute_value, substitution

    @staticmethod
    def compute_allowed_flag_matches(attribute_value):
        return re.finditer(
            r'''
                (?P<whitespace_only> \A [\s]* \Z )
                    |
                (?P<none_keyword> \A [\s]* NONE [\s]* \Z )
                    |
                [\s]*
                (?:
                    (?P<flag_letter> [a-z] ) = (?P<flag_name> [A-Z_]+ ) (?= [\s] | \Z )
                        |
                    (?P<invalid_syntax> [\S]+ )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_allowed_flags(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        flag_name_from_letter = {}

        for allowed_flag_match in ReplacementMaster.compute_allowed_flag_matches(attribute_value):
            if allowed_flag_match.group('whitespace_only') is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `` for attribute `allowed_flags`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            invalid_syntax = allowed_flag_match.group('invalid_syntax')
            if invalid_syntax is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `{invalid_syntax}` for attribute `allowed_flags`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            if allowed_flag_match.group('none_keyword') is not None:
                return

            flag_letter = allowed_flag_match.group('flag_letter')
            flag_name = allowed_flag_match.group('flag_name')
            flag_name_from_letter[flag_letter] = flag_name

        replacement.flag_name_from_letter = flag_name_from_letter

    @staticmethod
    def compute_apply_mode_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<apply_mode> SIMULTANEOUS | SEQUENTIAL )
                        |
                    (?P<invalid_value> [\s\S]*? )
                    )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE
        )

    @staticmethod
    def stage_apply_mode(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        apply_mode_match = ReplacementMaster.compute_apply_mode_match(attribute_value)

        invalid_value = apply_mode_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `apply_mode`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        apply_mode = apply_mode_match.group('apply_mode')
        replacement.apply_substitutions_simultaneously = apply_mode == 'SIMULTANEOUS'

    @staticmethod
    def compute_attribute_specifications_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<none_keyword> NONE )
                        |
                    (?P<empty_keyword> EMPTY )
                        |
                    (?P<attribute_specifications> [\S][\s\S]*? )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_attribute_specifications(
        replacement,
        attribute_value,
        rules_file_name,
        line_number_range_start,
        line_number,
    ):
        attribute_specifications_match = ReplacementMaster.compute_attribute_specifications_match(attribute_value)

        invalid_value = attribute_specifications_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `attribute_specifications`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if attribute_specifications_match.group('none_keyword') is not None:
            return

        if attribute_specifications_match.group('empty_keyword') is not None:
            replacement.attribute_specifications = ''
            return

        attribute_specifications = attribute_specifications_match.group('attribute_specifications')
        replacement.attribute_specifications = attribute_specifications

    @staticmethod
    def compute_closing_delimiter_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<closing_delimiter> [\S][\s\S]*? )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_closing_delimiter(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        closing_delimiter_match = ReplacementMaster.compute_closing_delimiter_match(attribute_value)

        invalid_value = closing_delimiter_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `closing_delimiter`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        closing_delimiter = closing_delimiter_match.group('closing_delimiter')
        replacement.closing_delimiter = closing_delimiter

    @staticmethod
    def compute_concluding_replacement_matches(attribute_value):
        return re.finditer(
            r'''
                (?P<whitespace_only> \A [\s]* \Z )
                    |
                (?P<none_keyword> \A [\s]* NONE [\s]* \Z )
                    |
                (?:
                    [#] (?P<id_> [a-z0-9-.]+ ) (?= [\s] | \Z )
                        |
                    (?P<invalid_syntax> [\S]+ )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    def stage_concluding_replacements(
        self,
        replacement,
        attribute_value,
        rules_file_name,
        line_number_range_start,
        line_number,
    ):
        concluding_replacements = []

        for concluding_replacement_match in ReplacementMaster.compute_concluding_replacement_matches(attribute_value):
            if concluding_replacement_match.group('whitespace_only') is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `` for attribute `concluding_replacements`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            invalid_syntax = concluding_replacement_match.group('invalid_syntax')
            if invalid_syntax is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `{invalid_syntax}` for attribute `concluding_replacements`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            if concluding_replacement_match.group('none_keyword') is not None:
                return

            concluding_replacement_id = concluding_replacement_match.group('id_')
            if concluding_replacement_id == replacement.id_:
                concluding_replacement = replacement
            else:
                try:
                    concluding_replacement = self._replacement_from_id[concluding_replacement_id]
                except KeyError:
                    ReplacementMaster.print_error(
                        f'undefined replacement `#{concluding_replacement_id}`',
                        rules_file_name,
                        line_number_range_start,
                        line_number,
                    )
                    sys.exit(GENERIC_ERROR_EXIT_CODE)

            concluding_replacements.append(concluding_replacement)

        replacement.concluding_replacements = concluding_replacements

    @staticmethod
    def compute_content_replacement_matches(attribute_value):
        return re.finditer(
            r'''
                (?P<whitespace_only> \A [\s]* \Z )
                    |
                (?P<none_keyword> \A [\s]* NONE [\s]* \Z )
                    |
                (?:
                    [#] (?P<id_> [a-z0-9-.]+ ) (?= [\s] | \Z )
                        |
                    (?P<invalid_syntax> [\S]+ )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    def stage_content_replacements(
        self,
        replacement,
        attribute_value,
        rules_file_name,
        line_number_range_start,
        line_number,
    ):
        content_replacements = []

        for content_replacement_match in ReplacementMaster.compute_content_replacement_matches(attribute_value):
            if content_replacement_match.group('whitespace_only') is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `` for attribute `content_replacements`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            invalid_syntax = content_replacement_match.group('invalid_syntax')
            if invalid_syntax is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `{invalid_syntax}` for attribute `content_replacements`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            if content_replacement_match.group('none_keyword') is not None:
                return

            content_replacement_id = content_replacement_match.group('id_')
            if content_replacement_id == replacement.id_:
                content_replacement = replacement
            else:
                try:
                    content_replacement = self._replacement_from_id[content_replacement_id]
                except KeyError:
                    ReplacementMaster.print_error(
                        f'undefined replacement `#{content_replacement_id}`',
                        rules_file_name,
                        line_number_range_start,
                        line_number,
                    )
                    sys.exit(GENERIC_ERROR_EXIT_CODE)

            content_replacements.append(content_replacement)

        replacement.content_replacements = content_replacements

    @staticmethod
    def compute_delimiter_conversion_matches(attribute_value):
        return re.finditer(
            r'''
                (?P<whitespace_only> \A [\s]* \Z )
                    |
                [\s]*
                (?:
                    (?P<delimiter>
                    (?P<delimiter_character> [\S] ) (?P=delimiter_character)?
                )
                    = (?P<tag_name> [a-z0-9]+ ) (?= [\s] | \Z )
                    |
                (?P<invalid_syntax> [\S]+ )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_delimiter_conversion(
        replacement,
        attribute_value,
        rules_file_name,
        line_number_range_start,
        line_number,
    ):
        tag_name_from_delimiter_length_from_character = {}

        for delimiter_conversion_match in ReplacementMaster.compute_delimiter_conversion_matches(attribute_value):
            if delimiter_conversion_match.group('whitespace_only') is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `` for attribute `delimiter_conversion`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            invalid_syntax = delimiter_conversion_match.group('invalid_syntax')
            if invalid_syntax is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `{invalid_syntax}` for attribute `delimiter_conversion`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            character = delimiter_conversion_match.group('delimiter_character')
            length = len(delimiter_conversion_match.group('delimiter'))
            tag_name = delimiter_conversion_match.group('tag_name')

            if character not in tag_name_from_delimiter_length_from_character:
                tag_name_from_delimiter_length_from_character[character] = {}

            tag_name_from_delimiter_length_from_character[character][length] = tag_name

        replacement.tag_name_from_delimiter_length_from_character = tag_name_from_delimiter_length_from_character

    @staticmethod
    def compute_ending_pattern_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<none_keyword> NONE )
                        |
                    (?P<ending_pattern> [\S][\s\S]*? )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_ending_pattern(
        replacement,
        attribute_value,
        rules_file_name,
        line_number_range_start,
        line_number,
    ):
        ending_pattern_match = ReplacementMaster.compute_ending_pattern_match(attribute_value)

        invalid_value = ending_pattern_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `ending_pattern`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if ending_pattern_match.group('none_keyword') is not None:
            return

        ending_pattern = ending_pattern_match.group('ending_pattern')

        try:
            ending_pattern_compiled = re.compile(ending_pattern, flags=re.ASCII | re.MULTILINE | re.VERBOSE)
        except re.error as pattern_exception:
            ReplacementMaster.print_error(
                f'bad regex pattern `{ending_pattern}`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            ReplacementMaster.print_traceback(pattern_exception)
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if len(ending_pattern_compiled.groupindex) > 0:
            ReplacementMaster.print_error(
                f'named capture groups not allowed in `ending_pattern`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        replacement.ending_pattern = ending_pattern

    @staticmethod
    def compute_epilogue_delimiter_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<none_keyword> NONE )
                        |
                    (?P<epilogue_delimiter> [\S][\s\S]*? )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_epilogue_delimiter(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        epilogue_delimiter_match = ReplacementMaster.compute_epilogue_delimiter_match(attribute_value)

        invalid_value = epilogue_delimiter_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `epilogue_delimiter`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if epilogue_delimiter_match.group('none_keyword') is not None:
            return

        epilogue_delimiter = epilogue_delimiter_match.group('epilogue_delimiter')
        replacement.epilogue_delimiter = epilogue_delimiter

    @staticmethod
    def compute_extensible_delimiter_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<extensible_delimiter>
                        (?P<extensible_delimiter_character> [\S] )
                        (?P=extensible_delimiter_character)*
                    )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_extensible_delimiter(
        replacement,
        attribute_value,
        rules_file_name,
        line_number_range_start,
        line_number,
    ):
        extensible_delimiter_match = ReplacementMaster.compute_extensible_delimiter_match(attribute_value)

        invalid_value = extensible_delimiter_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` not a character repeated for attribute `extensible_delimiter`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        extensible_delimiter_character = extensible_delimiter_match.group('extensible_delimiter_character')
        extensible_delimiter = extensible_delimiter_match.group('extensible_delimiter')
        extensible_delimiter_min_length = len(extensible_delimiter)

        replacement.extensible_delimiter_character = extensible_delimiter_character
        replacement.extensible_delimiter_min_length = extensible_delimiter_min_length

    @staticmethod
    def compute_negative_flag_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<none_keyword> NONE )
                        |
                    (?P<negative_flag_name> [A-Z_]+ )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_negative_flag(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        negative_flag_match = ReplacementMaster.compute_negative_flag_match(attribute_value)

        invalid_value = negative_flag_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `negative_flag`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if negative_flag_match.group('none_keyword') is not None:
            return

        negative_flag_name = negative_flag_match.group('negative_flag_name')
        replacement.negative_flag_name = negative_flag_name

    @staticmethod
    def compute_opening_delimiter_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<opening_delimiter> [\S][\s\S]*? )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_opening_delimiter(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        opening_delimiter_match = ReplacementMaster.compute_opening_delimiter_match(attribute_value)

        invalid_value = opening_delimiter_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `opening_delimiter`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        opening_delimiter = opening_delimiter_match.group('opening_delimiter')
        replacement.opening_delimiter = opening_delimiter

    @staticmethod
    def compute_positive_flag_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<none_keyword> NONE )
                        |
                    (?P<positive_flag_name> [A-Z_]+ )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_positive_flag(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        positive_flag_match = ReplacementMaster.compute_positive_flag_match(attribute_value)

        invalid_value = positive_flag_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `positive_flag`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if positive_flag_match.group('none_keyword') is not None:
            return

        positive_flag_name = positive_flag_match.group('positive_flag_name')
        replacement.positive_flag_name = positive_flag_name

    @staticmethod
    def compute_prohibited_content_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<none_keyword> NONE )
                        |
                    (?P<prohibited_content> BLOCKS | ANCHORED_BLOCKS )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_prohibited_content(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        prohibited_content_match = ReplacementMaster.compute_prohibited_content_match(attribute_value)

        invalid_value = prohibited_content_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `prohibited_content`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if prohibited_content_match.group('none_keyword') is not None:
            return

        prohibited_content = prohibited_content_match.group('prohibited_content')
        require_anchoring = prohibited_content == 'ANCHORED_BLOCKS'
        prohibited_content_regex = build_block_tag_regex(require_anchoring)
        replacement.prohibited_content_regex = prohibited_content_regex

    @staticmethod
    def compute_prologue_delimiter_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<none_keyword> NONE )
                        |
                    (?P<prologue_delimiter> [\S][\s\S]*? )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_prologue_delimiter(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        prologue_delimiter_match = ReplacementMaster.compute_prologue_delimiter_match(attribute_value)

        invalid_value = prologue_delimiter_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `prologue_delimiter`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if prologue_delimiter_match.group('none_keyword') is not None:
            return

        prologue_delimiter = prologue_delimiter_match.group('prologue_delimiter')
        replacement.prologue_delimiter = prologue_delimiter

    @staticmethod
    def compute_queue_position_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<none_keyword> NONE )
                        |
                    (?P<root_keyword> ROOT )
                        |
                    (?P<queue_position_type> BEFORE | AFTER )
                    [ ]
                    [#] (?P<queue_reference_id> [a-z-.]+ )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    def stage_queue_position(self, replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        queue_position_match = ReplacementMaster.compute_queue_position_match(attribute_value)

        invalid_value = queue_position_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `queue_position`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if queue_position_match.group('none_keyword') is not None:
            return

        if queue_position_match.group('root_keyword') is not None:
            if self._root_replacement_id is not None:
                ReplacementMaster.print_error(
                    f'root replacement already declared (`#{self._root_replacement_id}`)',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)
            replacement.queue_position_type = 'ROOT'
            replacement.queue_reference_replacement = None
            return

        queue_position_type = queue_position_match.group('queue_position_type')
        queue_reference_id = queue_position_match.group('queue_reference_id')

        if queue_reference_id == replacement.id_:
            ReplacementMaster.print_error(
                f'self-referential `queue_position`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        try:
            queue_reference_replacement = self._replacement_from_id[queue_reference_id]
        except KeyError:
            ReplacementMaster.print_error(
                f'undefined replacement `#{queue_reference_id}`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if queue_reference_replacement not in self._replacement_queue:
            ReplacementMaster.print_error(
                f'replacement `#{queue_reference_id}` not in queue',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        replacement.queue_position_type = queue_position_type
        replacement.queue_reference_replacement = queue_reference_replacement

    @staticmethod
    def compute_replacement_matches(attribute_value):
        return re.finditer(
            r'''
                (?P<whitespace_only> \A [\s]* \Z )
                    |
                (?P<none_keyword> \A [\s]* NONE [\s]* \Z )
                    |
                (?:
                    [#] (?P<id_> [a-z0-9-.]+ ) (?= [\s] | \Z )
                        |
                    (?P<invalid_syntax> [\S]+ )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    def stage_replacements(self, replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        matched_replacements = []

        for replacement_match in ReplacementMaster.compute_replacement_matches(attribute_value):
            if replacement_match.group('whitespace_only') is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `` for attribute `replacements`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            invalid_syntax = replacement_match.group('invalid_syntax')
            if invalid_syntax is not None:
                ReplacementMaster.print_error(
                    f'invalid specification `{invalid_syntax}` for attribute `replacements`',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

            if replacement_match.group('none_keyword') is not None:
                return

            matched_replacement_id = replacement_match.group('id_')
            if matched_replacement_id == replacement.id_:
                matched_replacement = replacement
            else:
                try:
                    matched_replacement = self._replacement_from_id[matched_replacement_id]
                except KeyError:
                    ReplacementMaster.print_error(
                        f'undefined replacement `#{matched_replacement_id}`',
                        rules_file_name,
                        line_number_range_start,
                        line_number,
                    )
                    sys.exit(GENERIC_ERROR_EXIT_CODE)

            matched_replacements.append(matched_replacement)

        replacement.replacements = matched_replacements

    @staticmethod
    def compute_starting_pattern_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<starting_pattern> [\S][\s\S]*? )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_starting_pattern(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        starting_pattern_match = ReplacementMaster.compute_starting_pattern_match(attribute_value)

        invalid_value = starting_pattern_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `starting_pattern`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        starting_pattern = starting_pattern_match.group('starting_pattern')

        try:
            starting_pattern_compiled = re.compile(starting_pattern, flags=re.ASCII | re.MULTILINE | re.VERBOSE)
        except re.error as pattern_exception:
            ReplacementMaster.print_error(
                f'bad regex pattern `{starting_pattern}`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            ReplacementMaster.print_traceback(pattern_exception)
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if len(starting_pattern_compiled.groupindex) > 0:
            ReplacementMaster.print_error(
                f'named capture groups not allowed in `starting_pattern`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        replacement.starting_pattern = starting_pattern

    @staticmethod
    def compute_syntax_type_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<syntax_type> BLOCK | INLINE )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_syntax_type(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        syntax_type_match = ReplacementMaster.compute_syntax_type_match(attribute_value)

        invalid_value = syntax_type_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `syntax_type`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        syntax_type = syntax_type_match.group('syntax_type')
        replacement.syntax_type_is_block = syntax_type == 'BLOCK'

    @staticmethod
    def compute_tag_name_match(attribute_value):
        return re.fullmatch(
            r'''
                [\s]*
                (?:
                    (?P<none_keyword> NONE )
                        |
                    (?P<tag_name> [a-z0-9]+ )
                        |
                    (?P<invalid_value> [\s\S]*? )
                )
                [\s]*
            ''',
            attribute_value,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_tag_name(replacement, attribute_value, rules_file_name, line_number_range_start, line_number):
        tag_name_match = ReplacementMaster.compute_tag_name_match(attribute_value)

        invalid_value = tag_name_match.group('invalid_value')
        if invalid_value is not None:
            ReplacementMaster.print_error(
                f'invalid value `{invalid_value}` for attribute `tag_name`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        if tag_name_match.group('none_keyword') is not None:
            return

        tag_name = tag_name_match.group('tag_name')
        replacement.tag_name = tag_name

    @staticmethod
    def compute_substitution_match(substitution):
        substitution_delimiters = re.findall('[-]{2,}[>]', substitution)
        if len(substitution_delimiters) == 0:
            return None

        longest_substitution_delimiter = max(substitution_delimiters, key=len)
        return re.fullmatch(
            fr'''
                [\s]*
                    (?:
                        "(?P<double_quoted_pattern> [\s\S]*? )"
                            |
                        '(?P<single_quoted_pattern> [\s\S]*? )'
                            |
                        (?P<bare_pattern> [\s\S]*? )
                    )
                [\s]*
                    {re.escape(longest_substitution_delimiter)}
                    [\s]*
                    (?:
                        (?P<cmd_version_keyword> CMD_VERSION )
                            |
                        (?P<cmd_name_keyword> CMD_NAME )
                            |
                        (?P<cmd_basename_keyword> CMD_BASENAME )
                            |
                        (?P<clean_url_keyword> CLEAN_URL )
                            |
                        "(?P<double_quoted_substitute> [\s\S]*? )"
                            |
                        '(?P<single_quoted_substitute> [\s\S]*? )'
                            |
                        (?P<bare_substitute> [\s\S]*? )
                    )
                [\s]*
            ''',
            substitution,
            flags=re.ASCII | re.VERBOSE,
        )

    @staticmethod
    def stage_ordinary_substitution(
        replacement,
        substitution,
        rules_file_name,
        cmd_name,
        line_number_range_start,
        line_number,
    ):
        substitution_match = ReplacementMaster.compute_substitution_match(substitution)
        if substitution_match is None:
            ReplacementMaster.print_error(
                f'missing delimiter `-->` in substitution `{substitution}`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        double_quoted_pattern = substitution_match.group('double_quoted_pattern')
        if double_quoted_pattern is not None:
            pattern = double_quoted_pattern
        else:
            single_quoted_pattern = substitution_match.group('single_quoted_pattern')
            if single_quoted_pattern is not None:
                pattern = single_quoted_pattern
            else:
                pattern = substitution_match.group('bare_pattern')

        if substitution_match.group('cmd_version_keyword') is not None:
            substitute = __version__
        elif substitution_match.group('cmd_name_keyword') is not None:
            substitute = cmd_name
        elif substitution_match.group('cmd_basename_keyword') is not None:
            substitute = extract_basename(cmd_name)
        elif substitution_match.group('clean_url_keyword') is not None:
            substitute = make_clean_url(cmd_name)
        else:
            double_quoted_substitute = substitution_match.group('double_quoted_substitute')
            if double_quoted_substitute is not None:
                substitute = double_quoted_substitute
            else:
                single_quoted_substitute = substitution_match.group('single_quoted_substitute')
                if single_quoted_substitute is not None:
                    substitute = single_quoted_substitute
                else:
                    substitute = substitution_match.group('bare_substitute')

        replacement.add_substitution(pattern, substitute)

    @staticmethod
    def stage_regex_substitution(
        replacement,
        substitution,
        rules_file_name,
        cmd_name,
        line_number_range_start,
        line_number,
    ):
        substitution_match = ReplacementMaster.compute_substitution_match(substitution)
        if substitution_match is None:
            ReplacementMaster.print_error(
                f'missing delimiter `-->` in substitution `{substitution}`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        double_quoted_pattern = substitution_match.group('double_quoted_pattern')
        if double_quoted_pattern is not None:
            pattern = double_quoted_pattern
        else:
            single_quoted_pattern = substitution_match.group('single_quoted_pattern')
            if single_quoted_pattern is not None:
                pattern = single_quoted_pattern
            else:
                pattern = substitution_match.group('bare_pattern')

        if substitution_match.group('cmd_version_keyword') is not None:
            substitute = escape_regex_substitute(__version__)
        elif substitution_match.group('cmd_name_keyword') is not None:
            substitute = escape_regex_substitute(cmd_name)
        elif substitution_match.group('cmd_basename_keyword') is not None:
            substitute = escape_regex_substitute(extract_basename(cmd_name))
        elif substitution_match.group('clean_url_keyword') is not None:
            substitute = escape_regex_substitute(make_clean_url(cmd_name))
        else:
            double_quoted_substitute = substitution_match.group('double_quoted_substitute')
            if double_quoted_substitute is not None:
                substitute = double_quoted_substitute
            else:
                single_quoted_substitute = substitution_match.group('single_quoted_substitute')
                if single_quoted_substitute is not None:
                    substitute = single_quoted_substitute
                else:
                    substitute = substitution_match.group('bare_substitute')

        try:
            pattern_compiled = re.compile(pattern, flags=re.ASCII | re.MULTILINE | re.VERBOSE)
        except re.error as pattern_exception:
            ReplacementMaster.print_error(
                f'bad regex pattern `{pattern}`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            ReplacementMaster.print_traceback(pattern_exception)
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        try:
            re.sub(pattern_compiled, substitute, '')
        except re.error as substitute_exception:
            ReplacementMaster.print_error(
                f'bad regex substitute `{substitute}` for pattern `{pattern}`',
                rules_file_name,
                line_number_range_start,
                line_number,
            )
            ReplacementMaster.print_traceback(substitute_exception)
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        replacement.add_substitution(pattern, substitute)

    def stage(
        self,
        class_name,
        replacement,
        attribute_name,
        attribute_value,
        substitution,
        rules_file_name,
        cmd_name,
        line_number_range_start,
        line_number,
    ):
        if substitution is not None:  # staging a substitution
            if class_name == 'OrdinaryDictionaryReplacement':
                ReplacementMaster.stage_ordinary_substitution(
                    replacement,
                    substitution,
                    rules_file_name,
                    cmd_name,
                    line_number_range_start,
                    line_number,
                )
            elif class_name == 'RegexDictionaryReplacement':
                ReplacementMaster.stage_regex_substitution(
                    replacement,
                    substitution,
                    rules_file_name,
                    cmd_name,
                    line_number_range_start,
                    line_number,
                )
            else:
                ReplacementMaster.print_error(
                    f'class `{class_name}` does not allow substitutions',
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
                sys.exit(GENERIC_ERROR_EXIT_CODE)

        else:  # staging an attribute declaration
            if attribute_name == 'allowed_flags':
                ReplacementMaster.stage_allowed_flags(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'apply_mode':
                ReplacementMaster.stage_apply_mode(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'attribute_specifications':
                ReplacementMaster.stage_attribute_specifications(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'closing_delimiter':
                self.stage_closing_delimiter(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'concluding_replacements':
                self.stage_concluding_replacements(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'content_replacements':
                self.stage_content_replacements(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'delimiter_conversion':
                ReplacementMaster.stage_delimiter_conversion(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'ending_pattern':
                ReplacementMaster.stage_ending_pattern(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'epilogue_delimiter':
                ReplacementMaster.stage_epilogue_delimiter(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'extensible_delimiter':
                ReplacementMaster.stage_extensible_delimiter(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'negative_flag':
                ReplacementMaster.stage_negative_flag(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'opening_delimiter':
                ReplacementMaster.stage_opening_delimiter(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'positive_flag':
                ReplacementMaster.stage_positive_flag(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'prohibited_content':
                ReplacementMaster.stage_prohibited_content(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'prologue_delimiter':
                ReplacementMaster.stage_prologue_delimiter(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'queue_position':
                self.stage_queue_position(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'replacements':
                self.stage_replacements(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'starting_pattern':
                ReplacementMaster.stage_starting_pattern(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'syntax_type':
                ReplacementMaster.stage_syntax_type(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )
            elif attribute_name == 'tag_name':
                ReplacementMaster.stage_tag_name(
                    replacement,
                    attribute_value,
                    rules_file_name,
                    line_number_range_start,
                    line_number,
                )

        return None, None, None, None
        # attribute_name, attribute_value, substitution, line_number_range_start

    def commit(self, class_name, replacement, rules_file_name, line_number):
        try:
            replacement.commit()
        except MissingAttributeException as exception:
            missing_attribute = exception.missing_attribute
            ReplacementMaster.print_error(
                f'missing attribute `{missing_attribute}` for {class_name}',
                rules_file_name,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        id_ = replacement.id_
        self._replacement_from_id[id_] = replacement

        queue_position_type = replacement.queue_position_type
        if queue_position_type is None:
            pass
        elif queue_position_type == 'ROOT':
            self._root_replacement_id = id_
            self._replacement_queue.append(replacement)
        else:
            queue_reference_replacement = replacement.queue_reference_replacement
            queue_reference_index = self._replacement_queue.index(queue_reference_replacement)
            if queue_position_type == 'BEFORE':
                insertion_index = queue_reference_index
            elif queue_position_type == 'AFTER':
                insertion_index = queue_reference_index + 1
            else:
                insertion_index = None
            self._replacement_queue.insert(insertion_index, replacement)

        return None, None, None, None, None, None
        # class_name, replacement, attribute_name, attribute_value, substitution,
        # line_number_range_start

    def legislate(self, replacement_rules, rules_file_name, cmd_name):
        if replacement_rules is None:
            return

        class_name = None
        replacement = None
        attribute_name = None
        attribute_value = None
        substitution = None
        line_number_range_start = None
        line_number = 0

        for line_number, line in enumerate(replacement_rules.splitlines(), start=1):
            if ReplacementMaster.is_whitespace_only(line):
                if attribute_name is not None or substitution is not None:
                    attribute_name, attribute_value, substitution, line_number_range_start = \
                        self.stage(
                            class_name,
                            replacement,
                            attribute_name,
                            attribute_value,
                            substitution,
                            rules_file_name,
                            cmd_name,
                            line_number_range_start,
                            line_number,
                        )
                if replacement is not None:
                    class_name, replacement, attribute_name, attribute_value, substitution, line_number_range_start = \
                        self.commit(
                            class_name,
                            replacement,
                            rules_file_name,
                            line_number,
                        )
                continue

            if ReplacementMaster.is_comment(line):
                continue

            rules_inclusion_match = ReplacementMaster.compute_rules_inclusion_match(line)
            if rules_inclusion_match is not None:
                if attribute_name is not None or substitution is not None:
                    attribute_name, attribute_value, substitution, line_number_range_start = \
                        self.stage(
                            class_name,
                            replacement,
                            attribute_name,
                            attribute_value,
                            substitution,
                            rules_file_name,
                            cmd_name,
                            line_number_range_start,
                            line_number,
                        )
                if replacement is not None:
                    class_name, replacement, attribute_name, attribute_value, substitution, line_number_range_start = \
                        self.commit(
                            class_name,
                            replacement,
                            rules_file_name,
                            line_number,
                        )
                self.process_rules_inclusion_line(
                    rules_inclusion_match,
                    rules_file_name,
                    cmd_name,
                    line_number,
                )
                continue

            class_declaration_match = \
                ReplacementMaster.compute_class_declaration_match(line)
            if class_declaration_match is not None:
                if attribute_name is not None or substitution is not None:
                    attribute_name, attribute_value, \
                        substitution, \
                        line_number_range_start = \
                        self.stage(
                            class_name,
                            replacement,
                            attribute_name,
                            attribute_value,
                            substitution,
                            rules_file_name,
                            cmd_name,
                            line_number_range_start,
                            line_number,
                        )
                if replacement is not None:
                    class_name, replacement, \
                        attribute_name, attribute_value, \
                        substitution, \
                        line_number_range_start = \
                        self.commit(
                            class_name,
                            replacement,
                            rules_file_name,
                            line_number,
                        )
                class_name, replacement, line_number_range_start = \
                    self.process_class_declaration_line(
                        class_declaration_match,
                        rules_file_name,
                        line_number,
                    )
                continue

            attribute_declaration_match = ReplacementMaster.compute_attribute_declaration_match(line)
            if attribute_declaration_match is not None:
                if attribute_name is not None or substitution is not None:
                    attribute_name, attribute_value, substitution, line_number_range_start = \
                        self.stage(
                            class_name,
                            replacement,
                            attribute_name,
                            attribute_value,
                            substitution,
                            rules_file_name,
                            cmd_name,
                            line_number_range_start,
                            line_number,
                        )
                attribute_name, attribute_value, line_number_range_start = \
                    ReplacementMaster.process_attribute_declaration_line(
                        attribute_declaration_match,
                        class_name,
                        replacement,
                        attribute_value,
                        rules_file_name,
                        line_number,
                    )
                continue

            substitution_declaration_match = ReplacementMaster.compute_substitution_declaration_match(line)
            if substitution_declaration_match is not None:
                if attribute_name is not None or substitution is not None:
                    attribute_name, attribute_value, substitution, line_number_range_start = \
                        self.stage(
                            class_name,
                            replacement,
                            attribute_name,
                            attribute_value,
                            substitution,
                            rules_file_name,
                            cmd_name,
                            line_number_range_start,
                            line_number,
                        )
                substitution, line_number_range_start = \
                    ReplacementMaster.process_substitution_declaration_line(
                        replacement,
                        substitution_declaration_match,
                        substitution,
                        rules_file_name,
                        line_number,
                    )
                continue

            continuation_match = ReplacementMaster.compute_continuation_match(line)
            if continuation_match is not None:
                attribute_value, substitution = \
                    ReplacementMaster.process_continuation_line(
                        continuation_match,
                        attribute_name,
                        attribute_value,
                        substitution,
                        rules_file_name,
                        line_number,
                    )
                continue

            ReplacementMaster.print_error(
                'invalid syntax\n\n' + CMD_REPLACEMENT_SYNTAX_HELP,
                rules_file_name,
                line_number,
            )
            sys.exit(GENERIC_ERROR_EXIT_CODE)

        # At end of file
        if attribute_name is not None or substitution is not None:
            self.stage(
                class_name,
                replacement,
                attribute_name,
                attribute_value,
                substitution,
                rules_file_name,
                cmd_name,
                line_number_range_start,
                line_number,
            )
        if replacement is not None:
            self.commit(class_name, replacement, rules_file_name, line_number + 1)

    def execute(self, string):
        if self._verbose_mode_enabled:
            replacement_queue_ids = [
                f'#{replacement.id_}'
                for replacement in self._replacement_queue
            ]
            print(f'Replacement queue: {replacement_queue_ids}\n\n\n\n')

        for replacement in self._replacement_queue:
            string = replacement.apply(string)

        return string


def compute_longest_common_prefix(strings):
    shortest_string = min(strings, key=len, default='')

    prefix = shortest_string
    while len(prefix) > 0:
        if all(string.startswith(prefix) for string in strings):
            break
        prefix = prefix[:-1]

    return prefix


def de_indent(string):
    """
    De-indent a string.

    Empty lines do not count towards the longest common indentation.
    Whitespace-only lines do count towards the longest common indentation,
    except for the last line, which, if whitespace-only,
    will have its whitespace erased.

    In contrast, `textwrap.dedent` will perform blank erasure
    of whitespace on all whitespace-only lines,
    even those lines which are not the last line.
    """
    string = re.sub(r'^ [^\S\n]+ \Z', '', string, flags=re.ASCII | re.MULTILINE | re.VERBOSE)
    indentations = re.findall(r'^ [^\S\n]+ | ^ (?! $ )', string, flags=re.ASCII | re.MULTILINE | re.VERBOSE)
    longest_common_indentation = compute_longest_common_prefix(indentations)
    string = re.sub(f'^ {re.escape(longest_common_indentation)}', '', string, flags=re.MULTILINE | re.VERBOSE)

    return string


def escape_regex_substitute(substitute):
    return substitute.replace('\\', r'\\')


def escape_attribute_value_html(value):
    """
    Escape an attribute value that will be delimited by double quotes.

    For speed, we make the following assumptions:
    - Entity names are any run of up to 31 letters.
      At the time of writing (2022-04-18),
      the longest entity name is `CounterClockwiseContourIntegral`
      according to <https://html.spec.whatwg.org/entities.json>.
      Actually checking is slow for very little return.
    - Decimal code points are any run of up to 7 digits.
    - Hexadecimal code points are any run of up to 6 digits.
    """
    value = \
        re.sub(
            '''
                [&]
                (?!
                    (?:
                        [a-zA-Z]{1,31}
                            |
                        [#] (?: [0-9]{1,7} | [xX] [0-9a-fA-F]{1,6} )
                    )
                    [;]
                )
            ''',
            '&amp;',
            value,
            flags=re.VERBOSE,
        )
    value = re.sub('<', '&lt;', value)
    value = re.sub('>', '&gt;', value)
    value = re.sub('"', '&quot;', value)

    return value


def compute_attribute_specification_matches(attribute_specifications):
    return re.finditer(
        r'''
            [\s]*
            (?:
                (?P<name> [^\s=]+ ) =
                (?:
                    "(?P<double_quoted_value> [\s\S]*? )"
                        |
                    '(?P<single_quoted_value> [\s\S]*? )'
                        |
                    (?P<bare_value> [\S]* )
                )
                    |
                [#] (?P<id_> [^\s"]+ )
                    |
                [.] (?P<class_> [^\s"]+ )
                    |
                [r] (?P<rowspan> [0-9]+ )
                    |
                [c] (?P<colspan> [0-9]+ )
                    |
                [w] (?P<width> [0-9]+ )
                    |
                [h] (?P<height> [0-9]+ )
                    |
                [-] (?P<delete_name> [\S]+ )
                    |
                (?P<boolean_name> [\S]+ )
            ) ?
            [\s]*
        ''',
        attribute_specifications,
        flags=re.ASCII | re.VERBOSE,
    )


ATTRIBUTE_NAME_FROM_ABBREVIATION = {
    '#': 'id',
    '.': 'class',
    'l': 'lang',
    'r': 'rowspan',
    'c': 'colspan',
    'w': 'width',
    'h': 'height',
    's': 'style',
}


def extract_attribute_name_and_value(attribute_specification_match):
    """
    Extract (at most) name and value.

    Specifically:
    - («name», «value») for a non-boolean attribute
    - («name», None) for a boolean attribute
    - («name»,) for an attribute to be omitted
    - None for an invalid attribute specification
    """
    name = attribute_specification_match.group('name')
    if name is not None:
        try:
            name = ATTRIBUTE_NAME_FROM_ABBREVIATION[name]
        except KeyError:
            pass

        double_quoted_value = attribute_specification_match.group('double_quoted_value')
        if double_quoted_value is not None:
            return name, double_quoted_value

        single_quoted_value = attribute_specification_match.group('single_quoted_value')
        if single_quoted_value is not None:
            return name, single_quoted_value

        bare_value = attribute_specification_match.group('bare_value')
        if bare_value is not None:
            return name, bare_value

    id_ = attribute_specification_match.group('id_')
    if id_ is not None:
        return 'id', id_

    class_ = attribute_specification_match.group('class_')
    if class_ is not None:
        return 'class', class_

    rowspan = attribute_specification_match.group('rowspan')
    if rowspan is not None:
        return 'rowspan', rowspan

    colspan = attribute_specification_match.group('colspan')
    if colspan is not None:
        return 'colspan', colspan

    width = attribute_specification_match.group('width')
    if width is not None:
        return 'width', width

    height = attribute_specification_match.group('height')
    if height is not None:
        return 'height', height

    delete_name = attribute_specification_match.group('delete_name')
    if delete_name is not None:
        return delete_name,

    boolean_name = attribute_specification_match.group('boolean_name')
    if boolean_name is not None:
        return boolean_name, None

    return None


def build_attributes_sequence(attribute_specifications, use_protection=False):
    """
    Convert CMD attribute specifications to an attribute sequence.

    CMD attribute specifications are of the following forms:
    ````
    «name»="«quoted_value»"
    «name»=«bare_value»
    #«id»
    .«class»
    r«rowspan»
    c«colspan»
    w«width»
    h«height»
    -«delete_name»
    «boolean_name»
    ````
    In the two forms with an explicit equals sign,
    the following abbreviations are allowed for `name`:
    - # for id
    - . for class
    - l for lang
    - r for rowspan
    - c for colspan
    - w for width
    - h for height
    - s for style

    If an attribute of the same name is specified multiple times,
    the latest specification shall prevail,
    except when `class` is specified multiple times,
    in which case the values will be appended.
    For example, `id=x #y .a .b name=value .=c class="d"`
    shall be converted to the attrbute sequence
    ` id="y" class="a b c d" name="value"`.
    """
    attribute_value_from_name = {}

    for attribute_specification_match in compute_attribute_specification_matches(attribute_specifications):
        name_and_value = extract_attribute_name_and_value(attribute_specification_match)

        if name_and_value is None:  # invalid attribute specification
            continue

        try:
            name, value = name_and_value
            if name == 'class':
                try:
                    attribute_value_from_name['class'] += f' {value}'
                except KeyError:
                    attribute_value_from_name['class'] = value
            else:
                attribute_value_from_name[name] = value
        except ValueError:  # attribute to be omitted
            name, = name_and_value
            attribute_value_from_name.pop(name, None)

    attribute_sequence = ''

    for name, value in attribute_value_from_name.items():
        if value is None:  # boolean attribute
            attribute_sequence += f' {name}'
        else:
            value = PlaceholderMaster.unprotect(value)
            value = escape_attribute_value_html(value)
            attribute_sequence += f' {name}="{value}"'

    if use_protection:
        attribute_sequence = PlaceholderMaster.protect(attribute_sequence)

    return attribute_sequence


BLOCK_TAG_NAMES = [
    'address',
    'article',
    'aside',
    'blockquote',
    'dd',
    'details',
    'dialog',
    'div',
    'dl',
    'dt',
    'fieldset',
    'figcaption',
    'figure',
    'footer',
    'form',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'header',
    'hgroup',
    'hr',
    'li',
    'main',
    'nav',
    'ol',
    'p',
    'pre',
    'section',
    'table',
    'tbody',
    'td',
    'tfoot',
    'th',
    'thead',
    'ul',
]


def build_block_tag_regex(require_anchoring):
    block_tag_name_regex = '|'.join(re.escape(tag_name) for tag_name in BLOCK_TAG_NAMES)
    after_tag_name_regex = fr'[\s{PlaceholderMaster.MARKER}>]'
    block_tag_regex = f'[<] [/]? (?: {block_tag_name_regex} ) {after_tag_name_regex}'

    if require_anchoring:
        block_anchoring_regex = build_block_anchoring_regex(syntax_type_is_block=True)
        return block_anchoring_regex + block_tag_regex
    else:
        return block_tag_regex


def build_block_anchoring_regex(syntax_type_is_block, capture_anchoring_whitespace=False):
    if syntax_type_is_block:
        if capture_anchoring_whitespace:
            return r'^ (?P<anchoring_whitespace> [^\S\n]* )'
        else:
            return r'^ [^\S\n]*'

    return ''


def build_maybe_hanging_whitespace_regex():
    return r'[^\S\n]* (?: \n (?P=anchoring_whitespace) [^\S\n]+ )?'


def build_flags_regex(allowed_flags, has_flags):
    if not has_flags:
        return ''

    flag_letters = \
        ''.join(
            re.escape(flag_letter)
            for flag_letter in allowed_flags.keys()
        )
    return f'(?P<flags> [{flag_letters}]* )'


def build_extensible_delimiter_opening_regex(extensible_delimiter_character, extensible_delimiter_min_length):
    character_regex = re.escape(extensible_delimiter_character)
    repetition_regex = f'{{{extensible_delimiter_min_length},}}'

    return f'(?P<extensible_delimiter> {character_regex}{repetition_regex} )'


def build_attribute_specifications_regex(
    attribute_specifications,
    require_newline,
    capture_attribute_specifications=True,
    allow_omission=True,
):
    if attribute_specifications is not None:
        if capture_attribute_specifications:
            braced_sequence_regex = r'\{ (?P<attribute_specifications> [^}]*? ) \}'
        else:
            braced_sequence_regex = r'\{ [^}]*? \}'

        if allow_omission:
            braced_sequence_regex = f'(?: {braced_sequence_regex} )?'
    else:
        braced_sequence_regex = ''

    if require_newline:
        block_newline_regex = r'\n'
    else:
        block_newline_regex = ''

    return braced_sequence_regex + block_newline_regex


def build_captured_character_class_regex(characters, capture_group_name):
    if len(characters) == 0:
        return None

    characters_escaped = ''.join(re.escape(character) for character in sorted(characters))
    character_class_regex = f'[{characters_escaped}]'

    return f'(?P<{capture_group_name}> {character_class_regex} )'


def build_content_regex(
    prohibited_content_regex=None,
    permitted_content_regex=r'[\s\S]',
    permit_empty=True,
    capture_group_name='content',
):
    if prohibited_content_regex is None:
        permitted_atom_regex = permitted_content_regex
    else:
        permitted_atom_regex = f'(?: (?! {prohibited_content_regex} ) {permitted_content_regex} )'

    if permit_empty:
        repetition = '*'
    else:
        repetition = '+'

    return f'(?P<{capture_group_name}> {permitted_atom_regex}{repetition}? )'


def build_extensible_delimiter_closing_regex():
    return '(?P=extensible_delimiter)'


def build_uri_regex(be_greedy):
    if be_greedy:
        greed = ''
    else:
        greed = '?'

    return fr'(?: [<] (?P<angle_bracketed_uri> [^>]*? ) [>] | (?P<bare_uri> [\S]+{greed} ) )'


def build_title_regex():
    return r'''(?: "(?P<double_quoted_title> [^"]*? )" | '(?P<single_quoted_title> [^']*? )' )'''


def none_to_empty_string(string):
    if string is not None:
        return string

    return ''


def extract_rules_and_content(cmd):
    """
    Extract replacement rules and main content from CMD file content.

    «delimiter» shall be 3-or-more percent signs on its own line.
    If the CMD file content is free of «delimiter»,
    all of it shall be parsed as «main_content».
    If the CMD file content contains «delimiter»,
    it shall be parsed as
            «replacement_rules»
            «delimiter»
            «main_content»
    according to the first occurrence of «delimiter».
    """
    match = \
        re.fullmatch(
            r'''
                (?:
                    (?P<replacement_rules> [\s\S]*? )
                    (?P<delimiter> ^ [%]{3,} )
                    \n
                ) ?
                (?P<main_content> [\s\S]* )
            ''',
            cmd,
            flags=re.ASCII | re.MULTILINE | re.VERBOSE,
        )

    replacement_rules = match.group('replacement_rules')
    main_content = match.group('main_content')

    return replacement_rules, main_content


def extract_basename(name):
    return re.sub(r'\A .* [/]', '', name, flags=re.VERBOSE)


def make_clean_url(cmd_name):
    return re.sub(
        r'(?P<last_separator> \A | [/] ) index \Z',
        r'\g<last_separator>',
        cmd_name,
        flags=re.VERBOSE,
    )


def extract_separator_normalised_cmd_name(cmd_file_name):
    cmd_name = re.sub(r'[.](cmd) \Z', '', cmd_file_name, flags=re.VERBOSE)
    separator_normalised_cmd_name = cmd_name.replace('\\', '/')

    return separator_normalised_cmd_name


STANDARD_RULES = \
r'''# STANDARD_RULES

PlaceholderMarkerReplacement: #placeholder-markers
- queue_position: ROOT

PlaceholderProtectionReplacement: #placeholder-protect

DeIndentationReplacement: #de-indent
- negative_flag: KEEP_INDENTED

OrdinaryDictionaryReplacement: #escape-html
- negative_flag: KEEP_HTML_UNESCAPED
* & --> &amp;
* < --> &lt;
* > --> &gt;

RegexDictionaryReplacement: #trim-whitespace
* \A [\s]* -->
* [\s]* \Z -->

RegexDictionaryReplacement: #reduce-whitespace
- positive_flag: REDUCE_WHITESPACE
* ^ [^\S\n]+ -->
* [^\S\n]+ $ -->
* [\s]+ (?= <br> ) -->
* [\n]+ --> \n

ExtensibleFenceReplacement: #literals
- queue_position: AFTER #placeholder-markers
- syntax_type: INLINE
- allowed_flags:
    u=KEEP_HTML_UNESCAPED
    i=KEEP_INDENTED
    w=REDUCE_WHITESPACE
- prologue_delimiter: <
- extensible_delimiter: `
- content_replacements:
    #escape-html
    #de-indent
    #trim-whitespace
    #reduce-whitespace
    #placeholder-protect
- epilogue_delimiter: >

RegexDictionaryReplacement: #code-tag-wrap
* \A --> <code>
* \Z --> </code>

ExtensibleFenceReplacement: #display-code
- queue_position: AFTER #literals
- syntax_type: BLOCK
- allowed_flags:
    u=KEEP_HTML_UNESCAPED
    i=KEEP_INDENTED
    w=REDUCE_WHITESPACE
- extensible_delimiter: ``
- attribute_specifications: EMPTY
- content_replacements:
    #escape-html
    #de-indent
    #reduce-whitespace
    #code-tag-wrap
    #placeholder-protect
- tag_name: pre

RegexDictionaryReplacement: #comments
- queue_position: AFTER #display-code
* [^\S\n]*
  [<]
    (?P<hashes> [#]+ )
      [\s\S]*?
    (?P=hashes)
  [>]
    -->

RegexDictionaryReplacement: #prepend-newline
* \A --> \n

ExtensibleFenceReplacement: #divisions
- queue_position: AFTER #comments
- syntax_type: BLOCK
- extensible_delimiter: ||
- attribute_specifications: EMPTY
- content_replacements:
    #divisions
    #prepend-newline
- tag_name: div

ExtensibleFenceReplacement: #blockquotes
- queue_position: AFTER #divisions
- syntax_type: BLOCK
- extensible_delimiter: ""
- attribute_specifications: EMPTY
- content_replacements:
    #blockquotes
    #prepend-newline
- tag_name: blockquote

PartitioningReplacement: #unordered-list-items
- starting_pattern: [-+*]
- attribute_specifications: EMPTY
- content_replacements:
    #prepend-newline
- ending_pattern: [-+*]
- tag_name: li

ExtensibleFenceReplacement: #unordered-lists
- queue_position: AFTER #blockquotes
- syntax_type: BLOCK
- extensible_delimiter: ==
- attribute_specifications: EMPTY
- content_replacements:
    #unordered-lists
    #unordered-list-items
    #prepend-newline
- tag_name: ul

PartitioningReplacement: #ordered-list-items
- starting_pattern: [0-9]+ [.]
- attribute_specifications: EMPTY
- content_replacements:
    #prepend-newline
- ending_pattern: [0-9]+ [.]
- tag_name: li

ExtensibleFenceReplacement: #ordered-lists
- queue_position: AFTER #unordered-lists
- syntax_type: BLOCK
- extensible_delimiter: ++
- attribute_specifications: EMPTY
- content_replacements:
    #ordered-lists
    #ordered-list-items
    #prepend-newline
- tag_name: ol

RegexDictionaryReplacement: #mark-table-headers-for-preceding-table-data
* \A --> ;{}
# Replaces `<th«attributes_sequence»>` with `;{}<th«attributes_sequence»>`,
# so that #table-data will know to stop before it.

PartitioningReplacement: #table-headers
- starting_pattern: [;]
- attribute_specifications: EMPTY
- content_replacements:
    #trim-whitespace
- ending_pattern: [;,]
- tag_name: th
- concluding_replacements:
    #mark-table-headers-for-preceding-table-data

PartitioningReplacement: #table-data
- starting_pattern: [,]
- attribute_specifications: EMPTY
- content_replacements:
    #trim-whitespace
- ending_pattern: [;,]
- tag_name: td

RegexDictionaryReplacement: #unmark-table-headers-for-preceding-table-data
* ^ [;] \{ \} <th (?P<bracket_or_placeholder_marker> [>\uF8FF] )
    -->
  <th\g<bracket_or_placeholder_marker>
# Replaces `;{}<th«attributes_sequence»>` with `<th«attributes_sequence»>`
# so that #mark-table-headers-for-preceding-table-data is undone.

PartitioningReplacement: #table-rows
- starting_pattern: [/]{2}
- attribute_specifications: EMPTY
- ending_pattern: [/]{2}
- content_replacements:
    #table-headers
    #table-data
    #unmark-table-headers-for-preceding-table-data
    #prepend-newline
- tag_name: tr

PartitioningReplacement: #table-head
- starting_pattern: [|][\^]
- attribute_specifications: EMPTY
- ending_pattern: [|][:_]
- content_replacements:
    #table-rows
    #prepend-newline
- tag_name: thead

PartitioningReplacement: #table-body
- starting_pattern: [|][:]
- attribute_specifications: EMPTY
- ending_pattern: [|][_]
- content_replacements:
    #table-rows
    #prepend-newline
- tag_name: tbody

PartitioningReplacement: #table-foot
- starting_pattern: [|][_]
- attribute_specifications: EMPTY
- content_replacements:
    #table-rows
    #prepend-newline
- tag_name: tfoot

ExtensibleFenceReplacement: #tables
- queue_position: AFTER #ordered-lists
- syntax_type: BLOCK
- extensible_delimiter: ''
- attribute_specifications: EMPTY
- content_replacements:
    #tables
    #table-head
    #table-body
    #table-foot
    #table-rows
    #prepend-newline
- tag_name: table

HeadingReplacement: #headings
- queue_position: AFTER #tables
- attribute_specifications: EMPTY

ExtensibleFenceReplacement: #paragraphs
- queue_position: AFTER #headings
- syntax_type: BLOCK
- extensible_delimiter: --
- attribute_specifications: EMPTY
- prohibited_content: BLOCKS
- content_replacements:
    #prepend-newline
- tag_name: p

ExtensibleFenceReplacement: #inline-code
- queue_position: AFTER #paragraphs
- syntax_type: INLINE
- allowed_flags:
    u=KEEP_HTML_UNESCAPED
    i=KEEP_INDENTED
    w=REDUCE_WHITESPACE
- extensible_delimiter: `
- attribute_specifications: EMPTY
- prohibited_content: ANCHORED_BLOCKS
- content_replacements:
    #escape-html
    #de-indent
    #trim-whitespace
    #reduce-whitespace
    #placeholder-protect
- tag_name: code

RegexDictionaryReplacement: #boilerplate
- queue_position: AFTER #inline-code
* \A -->
    <!DOCTYPE html>
    <html lang="%lang">
      <head>
        <meta charset="utf-8">
        %head-elements-before-viewport
        <meta name="viewport" content="%viewport-content">
        %head-elements-after-viewport
        <title>%title</title>
        <style>
          %styles
        </style>
      </head>
      <body>\n
* \Z -->
      </body>
    </html>\n

OrdinaryDictionaryReplacement: #boilerplate-properties
- queue_position: AFTER #boilerplate
* %lang --> en
* %head-elements-before-viewport -->
* %viewport-content --> width=device-width, initial-scale=1
* %head-elements-after-viewport -->
* %title --> Title
* %styles -->

OrdinaryDictionaryReplacement: #cmd-properties
- queue_position: AFTER #boilerplate-properties
* %cmd-version --> CMD_VERSION
* %cmd-name --> CMD_NAME
* %cmd-basename --> CMD_BASENAME
* %clean-url --> CLEAN_URL
- concluding_replacements:
    #placeholder-protect

RegexDictionaryReplacement: #boilerplate-protect
- queue_position: AFTER #cmd-properties
* <style>[\s]*?</style>[\s]* -->
* <style>[\s\S]*?</style> --> \g<0>
* <head>[\s\S]*?</head> --> \g<0>
- concluding_replacements:
    #reduce-whitespace
    #placeholder-protect

OrdinaryDictionaryReplacement: #backslash-escapes
- queue_position: AFTER #boilerplate-protect
* \\ --> \
* \" --> "
* \# --> #
* \& --> &amp;
* \' --> '
* \( --> (
* \) --> )
* \* --> *
* \< --> &lt;
* \> --> &gt;
* \[ --> [
* \] --> ]
* \_ --> _
* \{ --> {
* \| --> |
* \} --> }
* "\ " --> " "
* \t --> "	"
- concluding_replacements:
    #placeholder-protect

RegexDictionaryReplacement: #backslash-continuations
- queue_position: AFTER #backslash-escapes
* \\ \n [^\S\n]* -->

ReferenceDefinitionReplacement: #reference-definitions
- queue_position: AFTER #backslash-continuations
- attribute_specifications: EMPTY

SpecifiedImageReplacement: #specified-images
- queue_position: AFTER #reference-definitions
- attribute_specifications: EMPTY
- prohibited_content: BLOCKS

ReferencedImageReplacement: #referenced-images
- queue_position: AFTER #specified-images
- attribute_specifications: EMPTY
- prohibited_content: BLOCKS

RegexDictionaryReplacement: #suppress-scheme
- positive_flag: SUPPRESS_SCHEME
* \A [\S]+ [:] (?: [/]{2} )? -->

RegexDictionaryReplacement: #angle-bracket-wrap
- positive_flag: ANGLE_BRACKET_WRAP
* \A --> &lt;
* \Z --> &gt;
- concluding_replacements:
    #placeholder-protect

ExplicitLinkReplacement: #explicit-links
- queue_position: AFTER #referenced-images
- allowed_flags:
    b=ANGLE_BRACKET_WRAP
    s=SUPPRESS_SCHEME
- attribute_specifications: EMPTY
- content_replacements:
    #suppress-scheme
- concluding_replacements:
    #angle-bracket-wrap

SpecifiedLinkReplacement: #specified-links
- queue_position: AFTER #explicit-links
- attribute_specifications: EMPTY
- prohibited_content: BLOCKS

ReferencedLinkReplacement: #referenced-links
- queue_position: AFTER #specified-links
- attribute_specifications: EMPTY
- prohibited_content: BLOCKS

InlineAssortedDelimitersReplacement: #inline-semantics
- queue_position: AFTER #referenced-links
- delimiter_conversion:
    __=b
    _=i
    **=strong
    *=em
    ''=cite
    ""=q
- attribute_specifications: EMPTY
- prohibited_content: BLOCKS

RegexDictionaryReplacement: #escape-idle-html
- queue_position: AFTER #inline-semantics
* [&]
  (?!
    (?:
      [a-zA-Z]{1,31}
        |
      [#] (?: [0-9]{1,7} | [xX] [0-9a-fA-F]{1,6} )
    )
    [;]
  )
    --> &amp;
* [<] (?= [\s] ) --> &lt;

ReplacementSequence: #whitespace
- queue_position: AFTER #escape-idle-html
- replacements:
    #reduce-whitespace

PlaceholderUnprotectionReplacement: #placeholder-unprotect
- queue_position: AFTER #whitespace
'''


def cmd_to_html(cmd, cmd_file_name, verbose_mode_enabled=False):
    """
    Convert CMD to HTML.
    """

    replacement_rules, main_content = extract_rules_and_content(cmd)
    separator_normalised_cmd_name = extract_separator_normalised_cmd_name(cmd_file_name)

    replacement_master = ReplacementMaster(cmd_file_name, verbose_mode_enabled)
    replacement_master.legislate(
        STANDARD_RULES,
        rules_file_name='STANDARD_RULES',
        cmd_name=separator_normalised_cmd_name,
    )
    replacement_master.legislate(
        replacement_rules,
        rules_file_name=cmd_file_name,
        cmd_name=separator_normalised_cmd_name,
    )
    html = replacement_master.execute(main_content)

    return html


def is_cmd_file(file_name):
    return file_name.endswith('.cmd')


def extract_cmd_name(cmd_file_name_argument):
    """
    Extract name-without-extension from a CMD file name argument.

    Here, CMD file name argument may be of the form
    `«cmd_name».cmd`, `«cmd_name».`, or `«cmd_name»`.
    The path is normalised by resolving `./` and `../`.
    """

    cmd_file_name_argument = os.path.normpath(cmd_file_name_argument)
    cmd_name = re.sub(r'[.](cmd)? \Z', '', cmd_file_name_argument, flags=re.VERBOSE)

    return cmd_name


def generate_html_file(cmd_file_name_argument, verbose_mode_enabled, uses_command_line_argument):
    cmd_name = extract_cmd_name(cmd_file_name_argument)
    cmd_file_name = f'{cmd_name}.cmd'
    try:
        with open(cmd_file_name, 'r', encoding='utf-8') as cmd_file:
            cmd = cmd_file.read()
    except FileNotFoundError as file_not_found_error:
        if uses_command_line_argument:
            print(f'error: argument `{cmd_file_name_argument}`: file `{cmd_file_name}` not found', file=sys.stderr)
            sys.exit(COMMAND_LINE_ERROR_EXIT_CODE)
        else:
            error_message = f'file `{cmd_file_name}` not found for `{cmd_file_name}` in cmd_file_name_list'
            raise FileNotFoundError(error_message) from file_not_found_error

    html = cmd_to_html(cmd, cmd_file_name, verbose_mode_enabled)

    html_file_name = f'{cmd_name}.html'
    try:
        with open(html_file_name, 'w', encoding='utf-8') as html_file:
            html_file.write(html)
            print(f'success: wrote to `{html_file_name}`')
    except IOError:
        print(f'error: cannot write to `{html_file_name}`', file=sys.stderr)
        sys.exit(GENERIC_ERROR_EXIT_CODE)


DESCRIPTION = '''
    Convert Conway-Markdown (CMD) to HTML.
'''
CMD_FILE_NAME_HELP = '''
    Name of CMD file to be converted.
    Abbreviate as `file` or `file.` for increased productivity.
    Omit to convert all CMD files under the working directory.
'''
VERBOSE_MODE_HELP = '''
    run in verbose mode (prints every replacement applied)
'''


def parse_command_line_arguments():
    argument_parser = argparse.ArgumentParser(description=DESCRIPTION)
    argument_parser.add_argument(
        '-v', '--version',
        action='version',
        version=f'{argument_parser.prog} version {__version__}',
    )
    argument_parser.add_argument(
        '-x', '--verbose',
        dest='verbose_mode_enabled',
        action='store_true',
        help=VERBOSE_MODE_HELP,
    )
    argument_parser.add_argument(
        'cmd_file_name_arguments',
        default=[],
        help=CMD_FILE_NAME_HELP,
        metavar='file.cmd',
        nargs='*',
    )

    return argument_parser.parse_args()


def main():
    parsed_arguments = parse_command_line_arguments()
    cmd_file_name_arguments = parsed_arguments.cmd_file_name_arguments
    verbose_mode_enabled = parsed_arguments.verbose_mode_enabled

    if len(cmd_file_name_arguments) > 0:
        for cmd_file_name_argument in cmd_file_name_arguments:
            generate_html_file(cmd_file_name_argument, verbose_mode_enabled, uses_command_line_argument=True)
    else:
        cmd_file_names = [
            os.path.join(path, file_name)
            for path, _, file_names in os.walk(os.curdir)
            for file_name in file_names
            if is_cmd_file(file_name)
        ]
        for cmd_file_name in sorted(cmd_file_names):
            generate_html_file(cmd_file_name, verbose_mode_enabled, uses_command_line_argument=False)


if __name__ == '__main__':
    main()
