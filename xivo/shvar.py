"""Read / Write variables defined in bash format

Copyright (C) 2008  Proformatique

See limitations in documentation of load().

This module can be used to manage, in particular, most of the configuration
files that are under /etc/default/

"""

__version__ = "$Revision$ $Date$"
__license__ = """
    Copyright (C) 2008  Proformatique

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along
    with this program; if not, write to the Free Software Foundation, Inc.,
    51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA..
"""

import re
import string


FORBIDDEN_VARNAMES = ('IFS', '_')

VariableAssignmentMatch = re.compile('([A-Za-z_][A-Za-z0-9_]*)=(.*)$').match

ANSI_C_ESCAPED_CODE = {
    "a": "\a",
    "b": "\b",
    "e": "\x1B",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    # NOTE: \nnn \xHH and \cx handled specifically
    # see the SINGLE_QUOTED_ESC_{OCTA,HEXA,CTRL} states
}

class ShParseAssignState(object):
    """States for function load()"""
    __slots__ = ('name',)
    def __init__(self, name):
        self.name = name

UNQUOTED = ShParseAssignState('UNQUOTED')
ESCAPED = ShParseAssignState('ESCAPED')
DOUBLE_QUOTED = ShParseAssignState('DOUBLE_QUOTED')
SINGLE_QUOTED = ShParseAssignState('SINGLE_QUOTED')
SINGLE_QUOTED_WITH_ESCAPING = ShParseAssignState('SINGLE_QUOTED_WITH_ESCAPING')
FINISHED = ShParseAssignState('FINISHED')

SINGLE_QUOTED_ESC_OCTAL = ShParseAssignState('SINGLE_QUOTED_ESC_OCTAL')
SINGLE_QUOTED_ESC_HEXA = ShParseAssignState('SINGLE_QUOTED_ESC_HEXA')
SINGLE_QUOTED_ESC_CTRL = ShParseAssignState('SINGLE_QUOTED_ESC_CTRL')

DOLLAR_UNQUOTED_UNSUP = string.ascii_letters + string.digits + '-$_@*?!{([#"'
DOLLAR_QUOTED_UNSUP   = string.ascii_letters + string.digits + '-$_@*?!{([#'


class Error(Exception):
    """
    Base class for exceptions of this module.
    """
    pass


class NotAssignmentError(Error):
    """
    Exception raised when a line is not an assignment in the bash syntax.
    """
    pass


class ComplexStatementError(Error):
    """
    Exception raised when the statement being processed is not a simple
    assignment.
    """
    pass


class UnsupportedAssignmentError(Error):
    """
    The assignment statement has not yet been detected as invalid (and might
    well be valid) but is using shell features that are not available in this
    module.
    """
    pass


def load(lineseq):
    """
    Parse a sequence of lines, each of which contains only a single variable
    assignment statement.  Empty lines and trailing comments are also allowed.
    Return (reslst, resdct)
    
    @reslst is a list of (varname, value, rotl), one per line of @lineseq
    In each (varname, value, rotl) tuple:
        @varname and @value are either two strings or both None,
        @varname can not be an empty string,
        @varname is a variable name when it is a string,
        @value is the variable value when it is a string,
        @rotl is a string which contains the "rest of the line" (right
            stripped, and also left stripped iff @varname of the same tuple is
            None).
    
    @resdct == dict([(varname, value)
                     for (varname, value, rotl) in reslst
                     if varname])
    
    NOTE: This implementation is very limited.
    * It does not support any kind of substitution / expression; an exception
      is raised if one is detected.
    * The form $'string' with backslash escape sequences _is_ supported.
    * Each statement is limited to a single physical line.  Line continuations
      are _not_ supported.  <newline> in single or double quoted strings are
      _not_ supported.
    * On each non blank/comment line, if anything other than a single variable
      assignement statement is detected, an exception is raised.
    * If on a single line quotes are not correctly balanced, an exception is
      raised.
    * Arrays are not supported.
    
    KNOWN INCOMPATIBILITIES:
    * In bash 3.1.17(1)-release (Debian Etch):
    $ echo -ne $'\c'abc | hd
    00000000  07 62 63                                          |.bc|
    
    'a' disappear for no real reason - and the absence of matching single quote
    is not detected.  Probably a tiny bug in bash - or the spec. is evil.
    
    This implementation will raise an exception if you try to parse something
    like $'\c'abc.  With something like $'\c''abc there will be a mismatch
    between bash and this implementation: bash thinks there are two single
    quoted parts and the second if unfinished, while this implementation thinks
    $'\c'' is the only single quoted part and abc is unquoted.
    """
    reslst = []
    resdct = {}
    
    for linenum, line in enumerate(lineseq):
        
        unit = line.strip()
        
        if (not unit) or (unit[0] == '#'):
            reslst.append((None, None, unit))
            continue
        
        match_assign = VariableAssignmentMatch(unit)
        if not match_assign:
            raise NotAssignmentError("not an assignment: l%d: %r" % (linenum + 1, unit))
        varname, right_part = match_assign.groups()
        
        if varname in FORBIDDEN_VARNAMES:
            raise UnsupportedAssignmentError("unsupported variable name in assignment: l%d: %r" % (linenum + 1, unit))
        
        splitted_value = []
        pos_rotl = len(right_part)
        
        state = UNQUOTED
        can_tilde_expand = True
        skip_next = False
        esc_octal_count = 0
        esc_hexa_count = 0
        esc_ctrl_count = 0
        accu = 0
        semicolon_seen = False
        
        for pos, (char, next) in enumerate(map(None, right_part, right_part[1:])):
            
            if skip_next:
                skip_next = False
                continue
            
            if state is UNQUOTED:
                
                if char in ' \t;':
                    if char == ";":
                        semicolon_seen = True
                    pos_rotl = pos
                    state = FINISHED
                    continue
                elif char in "|&()<>":
                    raise ComplexStatementError("complex statements (beginning with an assignment) are not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                elif char == "'":
                    state = SINGLE_QUOTED
                    continue
                elif char == '"':
                    state = DOUBLE_QUOTED
                    continue
                elif char == "\\":
                    if next is None:
                        raise UnsupportedAssignmentError("continued lines are not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                    else:
                        state = ESCAPED
                        continue
                elif char == "$":
                    if next == "'":
                        state = SINGLE_QUOTED_WITH_ESCAPING
                        skip_next = True
                        continue
                    elif next in DOLLAR_UNQUOTED_UNSUP:
                        raise UnsupportedAssignmentError("parameter / arithmetic expansion and command substitution are not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                    else:
                        pass # handled as a normal char
                elif can_tilde_expand and char == "~":
                    raise UnsupportedAssignmentError("tilde expansion is not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                elif char == "`":
                    raise UnsupportedAssignmentError("command substitution is not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                elif char in "*?[":
                    raise UnsupportedAssignmentError("pathname expansion is not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                
                can_tilde_expand = (char == ':')
                splitted_value.append(char)
            
            elif state is ESCAPED:
                
                splitted_value.append(char)
                
                state = UNQUOTED
                can_tilde_expand = False
            
            elif state is DOUBLE_QUOTED:
                
                if char == '"':
                    state = UNQUOTED
                    can_tilde_expand = False
                    continue
                elif char == "\\":
                    if next is None:
                        raise UnsupportedAssignmentError("continued lines are not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                    elif next in '\\"$`':
                        splitted_value.append(next)
                        skip_next = True
                        continue
                    else:
                        pass # handled as a normal char
                elif char == "$":
                    if next in DOLLAR_QUOTED_UNSUP:
                        raise UnsupportedAssignmentError("parameter / arithmetic expansion and command substitution are not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                    else:
                        pass # handled as a normal char
                elif char == '`':
                    raise UnsupportedAssignmentError("command substitution is not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                
                splitted_value.append(char)
            
            elif state is SINGLE_QUOTED:
                
                if char == "'":
                    state = UNQUOTED
                    can_tilde_expand = False
                    continue
                
                splitted_value.append(char)
            
            elif state is SINGLE_QUOTED_WITH_ESCAPING:
                
                if char == "'":
                    state = UNQUOTED
                    can_tilde_expand = False
                    continue
                elif char == "\\":
                    if next in ANSI_C_ESCAPED_CODE:
                        splitted_value.append(ANSI_C_ESCAPED_CODE[next])
                        skip_next = True
                        continue
                    elif next in '01234567':
                        esc_octal_count = 0
                        accu = 0
                        state = SINGLE_QUOTED_ESC_OCTAL
                        continue
                    elif next == "x":
                        esc_hexa_count = 0
                        accu = 0
                        state = SINGLE_QUOTED_ESC_HEXA
                        continue
                    elif next == "c":
                        esc_ctrl_count = 0
                        state = SINGLE_QUOTED_ESC_CTRL
                        continue
                    else:
                        pass # handled as a normal char
                
                splitted_value.append(char)
            
            elif state is SINGLE_QUOTED_ESC_OCTAL:
                
                accu = accu * 8 + int(char, 8)
                esc_octal_count += 1
                
                if esc_octal_count == 3 or (next not in '01234567'):
                    splitted_value.append(chr(accu & 0xFF))
                    accu = 0
                    state = SINGLE_QUOTED_WITH_ESCAPING
            
            elif state is SINGLE_QUOTED_ESC_HEXA:
                
                if esc_hexa_count > 0:
                    accu = accu * 16 + int(char, 16)
                
                esc_hexa_count += 1
                
                if esc_octal_count == 3 or (next not in string.hexdigits):
                    if esc_hexa_count == 1:
                        splitted_value.append('\\')
                        splitted_value.append('x')
                    else:
                        splitted_value.append(chr(accu & 0xFF))
                    accu = 0
                    state = SINGLE_QUOTED_WITH_ESCAPING
            
            elif state is SINGLE_QUOTED_ESC_CTRL:
                
                if esc_ctrl_count == 0:
                    if next is None:
                        raise UnsupportedAssignmentError("continued lines are not supported - faulty line: l%d: %r" % (linenum + 1, unit))
                else:
                    splitted_value.append(chr(ord(char) & 31))
                    state = SINGLE_QUOTED_WITH_ESCAPING
                
                esc_ctrl_count += 1
            
            elif state is FINISHED:
                
                if char in " \t":
                    if next is None:
                        break
                    else:
                        continue
                elif char == '#':
                    break
                elif char == ';':
                    if semicolon_seen:
                        raise ComplexStatementError("multiple semicolons detected - faulty line: l%d: %r" % (linenum + 1, unit))
                    else:
                        semicolon_seen = True
                        continue
                else:
                    raise ComplexStatementError("complex and multiple statements (beginning with an assignment) are not supported - faulty line: l%d: %r" % (linenum + 1, unit))
        
        else:
            if state is UNQUOTED:
                state = FINISHED
            if state is not FINISHED:
                raise UnsupportedAssignmentError("state is %s but should be FINISHED - probably trying to parse a continued line - this is not supported - faulty line: l%d: %r" % (state.name, linenum + 1, unit))
        
        value = ''.join(splitted_value)
        rotl = right_part[pos_rotl:]
        
        reslst.append((varname, value, rotl))
        resdct[varname] = value
    
    return reslst, resdct