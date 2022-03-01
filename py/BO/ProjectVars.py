# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
# Computations from free columns, at the level where they are present for a project.
#
import ast
from typing import Optional, List

from .Vocabulary import Term


class ProjectVariables(object):
    """
        TODO: Load/save/edit from Project
    """


class VariableValidity(object):
    """
        Expression of a validity interval.
    """

    def __init__(self, expr: str, min_val: Optional[float] = None, max_val: Optional[float] = None,
                 excluded_val: Optional[float] = None):
        self.expr = expr
        self.min_val = min_val
        self.max_val = max_val
        self.excluded = excluded_val

    def passes(self, a_val: float) -> bool:
        if self.min_val is not None and self.max_val is not None:
            return self.min_val <= a_val <= self.max_val
        if self.excluded is not None:
            return a_val != self.excluded
        return True


class ProjectVar(object):
    """
        Examples:
            "4.0/3.0*math.pi*(major/2*pixel_size)*(minor/2*pixel_size)**2"
    """

    def __init__(self, var_formula: str, term: Term, unit: Term,
                 valid_if: Optional[VariableValidity] = None):
        """
            Description of a project variable.
        :param var_formula: The formula for computing the variable.
        :param term: Definition of the formula output, i.e. of the variable.
        :param unit: Unit for the variable. Mandatory but there are unit-less units :)
        """
        self.formula = var_formula
        self.term = term
        self.unit = unit
        self.validator: Optional[VariableValidity] = valid_if
        self.variable_names = self._extract_variable_names()
        self.code = self._compile()

    def _extract_variable_names(self) -> List[str]:
        """
            Analyze the formula from syntactic point of view and extract variables.
        """
        try:
            formula_ast = ast.parse(self.formula, '<formula>', 'eval')
        except Exception as e:
            # Basically anything can happen here
            raise TypeError(str(e))
        ret = []
        for node in ast.walk(formula_ast):
            if isinstance(node, ast.Name):
                ret.append(node.id)
        return ret

    def _compile(self):
        return compile(self.formula, '<formula>', 'eval')

    def is_valid(self, a_val):
        if self.validator is not None:
            return self.validator.passes(a_val)
        return True
