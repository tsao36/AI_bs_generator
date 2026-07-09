""" Basic update (write) funcitons """

import inspect
import logging
from .utils import argumentize_query, deargumentize_query
from ._exceptions import UpdateHasNoEffect

log = logging.getLogger("DatabaseAPI")


def insert_to_table(self, table, value_dict):
    """Insert multiple values into a single new row in a table.

    Args:
        table (str): The table to insert into
        value_dict (dict): A dictionary of values to insert, formatted as {column_name: value}

    Returns:
        int: The ID of the newly inserted row if it has an auto-generated ID, otherwise None
    """
    value_dict = dict((k, v) for k, v in value_dict.items() if v is not None)  # removes null items
    keys = [f"[{x}]" for x in value_dict.keys()]
    values = value_dict.values()
    query_string = f"INSERT INTO [{table}] ({', '.join(keys)}) VALUES ({(','.join(['?' for key in keys]))})"
    with self.get_connection() as connection:
        cursor = self.smart_execute(connection.cursor(), query_string, *values)
        return self.smart_execute(cursor, "SELECT @@IDENTITY").fetchone()[0]


def update_table(self, table, key, value_dict, fail_if_no_effect=False, where=None):
    """Update a row in a table.

    Args:
        table (str): The table to update in.
        key ((str | int) | tuple): The identifier of the row to update.
                            Either a key to search in the primary table key, or a tuple containing key and value
                            with the key being the table key to search, and the value being the search query.
                            Can be None if a 'where' override is supplied

        value_dict (dict): A dictionary of values to update, formatted as {column_name: value}
        fail_if_no_effect (bool): Whether to raise an error if update statement had no effect.
        where (str): Override the WHERE argument of the query

    Raises:
        UpdateHasNoEffect: If there was no effect and fail_if_no_effect is True

    Returns:
        int: How many rows were updated
    """
    if not value_dict:  # Make sure it's not an empty dict since that'll break
        value_error = "The update function received no values to updated!"
        if fail_if_no_effect:
            raise ValueError(value_error)
        log.error(value_error)
        return 0
    update_string = ", ".join([f"[{k}]=?" for k in value_dict])
    arg_list = list(value_dict.values())
    if not where:
        if not key:
            raise ValueError("You must either supply a key or a WHERE override!")

        if isinstance(key, (str, int)):
            key_name = f"[{self.get_primary_key(table)}]"
            key_value = key
            if not key_name:
                raise Exception(f'Table "{table}" does not have a primary key and a column name was not supplied!')

        elif isinstance(key, dict):
            caller = inspect.stack()[1].function
            log.warning("%s using deprecated API: update_table with dict as key. Please update to tuple!", caller)
            key_name = f"[{list(key.keys())[0]}]"
            key_value = key[key_name]

        elif isinstance(key, tuple):
            key_name = key[0]
            key_value = key[1]

        else:
            raise TypeError(
                f"{type(key)} is not a valid type for the key attribute! Use either a string, an int, or a tuple!"
            )

        where = f"{key_name}=?"
        arg_list.append(key_value)

    query_string = f"UPDATE [{table}] SET {update_string} WHERE {where}"
    with self.get_connection() as connection:
        cursor = self.smart_execute(connection.cursor(), query_string, arg_list)
        if (not cursor.rowcount) and fail_if_no_effect:
            raise UpdateHasNoEffect(deargumentize_query(query_string, list(value_dict.values()) + [key_value]))
        return cursor.rowcount


def delete_row(self, table, key_name=None, key_value=None, where=None, fail_if_no_effect=False):
    """Delete a single row in a given table.
    Args:
        table (str): Name of the table to delete the row from
        key_name (str): Name of the column to filter the table by
        key_value (str): The value to filter the table by
        where (str): Override the where statement.
                     If key_name and key_value are also supplied, they will be ignored.
        fail_if_no_effect (bool): Whether to raise an error if update statement had no effect.

    Raises:
        UpdateHasNoEffect: If there was no effect and fail_if_no_effect is True

    Returns:
        int: How many rows were updated
    """
    if not where:
        if key_name and key_value:
            where = f"{key_name} = '{key_value}'"
        else:
            raise ValueError("Either a key name and value are required or a 'where' statement!")

    query_string = f"DELETE FROM [{table}] WHERE {where}"

    with self.get_connection() as connection:
        query_string, arg_list = argumentize_query(query_string)
        cursor = self.smart_execute(connection.cursor(), query_string, arg_list)
        if (not cursor.rowcount) and fail_if_no_effect:
            raise UpdateHasNoEffect(deargumentize_query(query_string, arg_list))
        return cursor.rowcount


def delete_all_rows(self, table):
    """Delete all rows in a given table.
    Args:
        table (str): Name of the table to delete all rows from
    """
    with self.get_connection() as connection:
        self.smart_execute(connection.cursor(), f"DELETE FROM [{table}]")
