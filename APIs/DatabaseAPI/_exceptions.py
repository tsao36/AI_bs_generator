""" Exceptions """


class NoResultsInTable(Exception):
    """Exception for when there are no results to a generic query in a specific table"""

    def __init__(self, table, query):
        self.query = query
        self.table = table
        super().__init__(f'Query "{query}" returned no results in table "{table}!')


class NoResultsForQuery(Exception):
    """Exception for when there are no results to a complex query"""

    def __init__(self, query):
        self.query = query
        super().__init__(f'Query "{query}" returned no results!')


class UpdateHasNoEffect(Exception):
    """
    Exception for when an update or delete operation has no effect, usually because the WHERE statements returned empty
    """

    def __init__(self, query):
        self.query = query
        super().__init__(f'Query "{query}" had no effect! This is usually because the WHERE statement returned empty.')
