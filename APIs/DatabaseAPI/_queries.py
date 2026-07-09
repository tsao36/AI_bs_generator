""" Basic query (read only) functions """

from .utils import argumentize_query


def query_table(
    self,
    table,
    select="*",
    where=None,
    order_by=None,
    order_dir="DESC",
    offset=None,
    next_rows=None,
    group_by=None,
    limit=None,
):
    """Query a table for data, supporting many options such as ordering, pagination, and search.

    Args:
        table (str): Name of table to query
        select (str): The select column query
        where (str, optional): The SQL search query (what comes after WHERE).
        order_by (str, optional): Column name to order by. Defaults to None.
        order_dir (str, optional): Order direction. Defaults to "DESC".
        offset (int, optional): Pagination offset (what row to start in).
                                If used, "next_rows" is also required.
                                Defaults to None.
        next_rows (int, optional): How many rows to take after the offset. Required if "Offset" is set.
                                    Defaults to None.
        group_by (str, optional): Adds a "GROUP BY" section to the query.
        limit (int, optional): Only select the top N rows. Defaults to None (select all).

    Returns:
        list: A list of rows, each an object in the form of "column_name: value"
    """
    limit_string = f"TOP {limit} " if limit else ""
    query_string = f"SELECT {limit_string}{select} FROM [{table}]"

    if where:
        query_string += f" WHERE {where}"

    if order_by:
        query_string += f" ORDER BY {order_by} {order_dir}"

    if order_by and offset is not None and next_rows is not None:  # Has to be "not None" since it can be 0
        query_string += f" OFFSET {offset} ROWS FETCH NEXT {next_rows} ROWS ONLY"

    if group_by:
        query_string += f" GROUP BY {group_by}"

    query_string, arg_list = argumentize_query(query_string)
    with self.get_connection() as connection:
        cursor = self.smart_execute(connection.cursor(), query_string, *arg_list)
        col_names = [x[0] for x in cursor.description]
        rows = []
        for row in cursor:
            rows.append(dict(zip(col_names, row)))
        return rows


def get_single_row(self, table, primary_key=None, row_id=None, select="*", where=None, order_by=None, order_dir="DESC"):
    """Gets an entire row's worth of data.

    Args:
        table (str|list): The name of the table to query
        primary_key (str): The name of the column to filter by.
                           Must be supplied if "where" is not used.
        row_id (str): The value of the column to filter by.
                      Must be supplied if "where" is not used.
        select (str): The columns to select by (defaults to "*").
        where (str): Override the query with a "where" expression.
                     The reason it's an override instead of a replacement for primary_key and row_id is to maintain
                     backwards compatibility.
        order_by (str): Optional. Use this column to order the results (in case there is more than one match).
        order_dir (str): The direction to order the results by (ASC for ascending, DESC for descending)

    Returns:
        dict: A dictionary where the column name is the key. An empty dict if row was not found.
    """
    if not where:
        # We need specific None here in case of boolean/int
        if primary_key is not None and row_id is not None:
            where = where if where else f"[{primary_key}]='{row_id}'"
        elif primary_key is not None or row_id is not None:  # One is supplied, the other wasn't
            raise ValueError(
                "If 'where' is not supplied, either both 'primary_key' and 'row_id' must be supplied or neither!'"
            )
        # If we reached here - neither where, primary key, nor row_id were supplied - and that's ok.
    where = f" WHERE {where}" if where else ""
    order = f" ORDER BY {order_by} {order_dir}" if order_by and order_dir else ""

    with self.get_connection() as connection:
        if isinstance(table, str):
            query_string = f"SELECT {select} FROM [{table}]{where}{order}"
        elif isinstance(table, list):
            query_string = ""
            for item in table:
                if query_string != "":
                    query_string += " else "
                query_string += f"IF EXISTS (SELECT 1 FROM {item}{where}) SELECT {select} from {item}{where}{order}"
        query_string, args_list = argumentize_query(query_string)
        cursor = self.smart_execute(connection.cursor(), query_string, *args_list)
        if not cursor.description or not (result := cursor.fetchone()):
            return {}
        col_names = [x[0] for x in cursor.description]
        return dict(zip(col_names, result))


def get_top_n_rows(
    self, table: str, num_of_row: int, order_by: str, order_dir: str = "DESC", where: str = None, select: str = "*"
):
    """Get the top `num_of_row` rows from a query result.
    Returns an empty list if no results are found, or the number of found results if lower than `num_of_rows`.

    Args:
        table (str): The name of the table
        num_of_row (int): The number of rows to return.
        order_by (str): The column name to order the results by.
        order_dir (str, optional): The order to sort by (`ASC` or `DESC`). Defaults to `DESC`.
        where (str, optional): Additional `WHERE` clause to query results by. Defaults to None.
        select (str, optional): The columns to select from the rows. Defaults to `*` which means all.

    Raises:
        TypeError: If the `num_of_rows` is not a number.

    Returns:
        list: A list of the top `num_of_rows` matching rows, each a dict.
    """
    if not isinstance(num_of_row, int):
        raise TypeError(f'"num_of_rows" must be an int, not a {type(num_of_row).__name__}')

    if order_dir not in ("ASC", "DESC"):
        raise ValueError(f'Only "ASC" and "DESC" are valid values for order_by, not "{order_by}"')

    return self.simple_query(
        f"SELECT TOP {num_of_row} {select} FROM [{table}] {where if where else ''} ORDER BY {order_by} {order_dir}",
        return_type=dict,
    )


def get_top_row(self, table: str, order_by: str, order_dir: str = "DESC", where: str = None, select: str = "*"):
    """Get the top row from a query result.
    Returns an empty dict if no results are found, or the number of found results if lower than `num_of_rows`.

    Args:
        table (str): The name of the table
        order_by (str): The column name to order the results by.
        order_dir (str, optional): The order to sort by (`ASC` or `DESC`). Defaults to `DESC`.
        where (str, optional): Additional `WHERE` clause to query results by. Defaults to None.
        select (str, optional): The columns to select from the rows. Defaults to `*` which means all.

    Returns:
        dict: The top row found in the query, or None if not found
    """
    results = self.get_top_n_rows(table, 1, order_by, order_dir, where, select)
    if results:
        return results[0]
    return None


def simple_query(self, query_string, return_type=list):
    """
    Perform a simple SQL query and return the results as a list of rows (each row a list of values).

    Args:
        query_string (str): A simple valid SQL query.
        return_type (type): The type to return results as.
                            Options are:
                                list (default): A list of matching rows, each row a list of values in order
                                dict: A list of matching rows, each row a dictionary of {column: value}

    Returns:
        [[<values>]]: A list of matching rows, each row a list of values in order
    or
        [{column: value}]: dict: A list of matching rows, each row a dictionary of {column: value}

    Raises:
        TypeError: If the return_type is not a type
        ValueError: If the return_type is of a non-supported type
    """
    if not isinstance(return_type, type):
        raise TypeError("return_type must be a type, not a variable!")
    with self.get_connection() as connection:
        query_string, args_list = argumentize_query(query_string)
        cursor = self.smart_execute(connection.cursor(), query_string, *args_list)
        rows = cursor.fetchall()
        if return_type == list:
            return [list(x) for x in rows]
        if return_type == dict:
            col_names = [x[0] for x in cursor.description]
            return list(dict(zip(col_names, list(result))) for result in rows)
        raise ValueError(f"Unsupported return type: {return_type.__name__}")


def get_column_names(self, table):
    """Returns the column names for the given table

    Args:
        table (str|list): Table or list of tables to check column names for

    Returns:
        list: A list of strings - the columns names - in no particular order.
    """
    if isinstance(table, str):
        table = [table]

    query_string = "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE ("
    for i, table_name in enumerate(table):
        if i != 0:
            query_string += "OR "
        query_string += f"TABLE_NAME = '{table_name}' "
    query_string += ") AND TABLE_SCHEMA = 'dbo'"
    with self.get_connection() as connection:
        query_string, args_list = argumentize_query(query_string)
        cursor = self.smart_execute(connection.cursor(), query_string, *args_list)
        return [row[0] for row in cursor.fetchall()]


def get_row_count(self, table, where=None):
    """Gets the number of rows in a given table.

    Args:
        table (str): The table to measure.
        where (str, optional): The SQL search query (what comes after WHERE).

    Returns:
        int: The number of rows in the supplied table.
    """
    with self.get_connection() as connection:
        query_string = f"SELECT COUNT(*) FROM [{table}]"
        if where:
            query_string += f" WHERE {where}"
        query_string, args_list = argumentize_query(query_string)
        cursor = self.smart_execute(connection.cursor(), query_string, *args_list)
        return int(cursor.fetchone()[0])


def get_column_values(
    self, table, column_name, where=None, order_by=None, order_type="DESC", group_by=None, distinct=False
):
    """Gets a list of all the values in a given column, with optional ordering.

    Args:
        table (str): The table to search in
        column_name (str): Name of the column to query
        where (str, optional): The SQL search query (what comes after WHERE).
        order_by (str, optional): [description]. The column name to order by.
        order_type (str, optional): Which direction to order in (ASC or DESC). Defaults to DESC.
        group_by (str, optional): Adds a "GROUP BY" section to the query.
        distinct (bool, optional): Whether or not to return distinct values. Defaults to False.

    Returns:
        list: List of strings representing the values in the provided column.
    """
    with self.get_connection() as connection:
        query_string = f"SELECT {'DISTINCT ' if distinct else ''}{column_name} FROM [{table}]"
        if where:
            query_string += f" WHERE {where}"

        if order_by:
            query_string += f" ORDER BY {order_by} {order_type}"

        if group_by:
            query_string += f" GROUP BY {group_by}"

        query_string, args_list = argumentize_query(query_string)
        cursor = self.smart_execute(connection.cursor(), query_string, *args_list)
        values = []
        for row in cursor:
            values.append(getattr(row, column_name))
        return values


def get_primary_key(self, table):
    """Get the name of the primary key of a given table.

    Args:
        table (str): The table to query.

    Returns:
        str: The primary key
    """
    with self.get_connection() as connection:
        cursor = connection.cursor()
        cursor.primaryKeys(table=table)
        return cursor.fetchone()[3]  # 3 is the index of the key name
