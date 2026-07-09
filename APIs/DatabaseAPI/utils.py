""" Generic utility functions """

import re


def argumentize_query(query):
    """A function that converts SQL queries to pyodbc compliant queries and their accompanying values.
    For example, the query:
        FROM table SELECT * WHERE foo = 'bar' AND lorem = 'ipsum'
    Will be converted to:
        FROM table SELECT * WHERE foo = ? AND lorem = ?
        ["bar", "ipsum"]
    Args:
        query(str): The original SQL query
    Returns (str, list): The resulting query and its accompanying argument list.
                        If no matches were found (read: the query was without parameters) the list will be empty.
    """
    result_query = re.sub(r"'([^']*)'", "?", query)
    query_args = [*re.findall(r"'([^']*)'", query)]  # Unpack the results into a dictionary
    return result_query, query_args


def deargumentize_query(query, args):
    """Turns the argumentized query back into a normal string.

    Args:
        query (str): The argumentized query
        args (list): The list of arguments

    Returns:
        str: A single query
    """
    for arg in args:
        query = query.replace("?", f"'{arg}'", 1)
    return query
