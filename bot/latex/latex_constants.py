# -*- coding: utf-8 -*-

"""
Этот файл содержит общие константы, используемые в разных частях проекта.
"""

# Общая преамбула для всех генерируемых LaTeX-документов
LATEX_PREAMBLE_TEMPLATE = r"""
\documentclass{{article}}
\usepackage[active,tightpage]{{preview}}
\usepackage{{amsmath}}
\usepackage{{fontspec}}
\usepackage[{lang}]{{babel}} % Язык по умолчанию, может быть переопределен
\usepackage{{cancel}}
\usepackage{{ulem}}
\usepackage{{xcolor}}
\usepackage{{hyperref}}

\setmainfont{{Arial}}[
    Path=./Fonts/, Extension=.TTF,
    UprightFont=*, BoldFont=*BD,
    ItalicFont=*I, BoldItalicFont=*BI
]
\pagenumbering{{gobble}}
\begin{{document}}
"""
