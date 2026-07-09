"""
A module containing all the LDAP related methods.
Documentation on Intel's LDAP implementation can be found in
https://wiki.ith.intel.com/display/ActiveDirectory/Application+Integration
"""

import re
import os
import sys
from ldap3 import Connection, Server

sys.path.append(os.path.join(os.path.dirname(__file__)))
from Sherlock import LDAP  # pylint: disable=wrong-import-position

EMAIL_PATTERN = r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Z|a-z]{2,})\b"
DISPLAY_NAME_PATTERN = r"(.+\S)\s*,\s*(.+)"  # Display name in the <last, first> format. Accommodates multi-parts names.


def query_ldap(query, users_only=True):
    """Searches LDAP for a generic query.
    Currently searches in mail, display name, and given name.

    Args:
        query (str): The search query
        users_only (bool): Searching in user objects only (read: not PDLs)
                           Defaults to True for backwards compatibility

    Returns:
        list<dict>: A list of dicts, each a result containing display_name, email, user_name
    """
    server = Server(LDAP.server, port=LDAP.port, use_ssl=True)
    connection = Connection(server, user=LDAP.username, password=LDAP.password, auto_bind=True)

    # Global fix for sys_windrvbuild's shenanigans.
    # LDAP only knows "sys_windrvbuild" while Gerrit only knows "windrvbuild".
    if query == "windrvbuild@intel.com":
        query = "sys_windrvbuild@intel.com"

    # Construct specific filter based on query type
    if " " in query and "," not in query:  # Probably <First Last> format
        inner_filter = f"(mail={query.replace(' ', '.')}*)"  # We don't support that, convert it to email with wildcard
    elif email_match := re.match(EMAIL_PATTERN, query):
        inner_filter = f"(mail={query})"
    else:
        inner_filter = f"(|(displayName={query}*)(givenName={query}*)(sAMAccountName={query}*))"

    pdl_filter = "" if users_only else "(&(objectcategory=group)(objectclass=group))"
    search_filter = f"(&(|(&(objectcategory=person)(objectclass=user)){pdl_filter}){inner_filter})"
    connection.search(
        search_base=LDAP.baseedn,
        search_filter=search_filter,
        attributes=["mail", "displayName", "sAMAccountName"],
    )
    responses = []

    for entry in connection.entries:
        responses.append(
            {"display_name": str(entry.displayName), "email": str(entry.mail), "user_name": str(entry.sAMAccountName)}
        )

    # Faceless account fallback
    if (not responses) and email_match:  # If an email was searched and no match was found, try just the prefix
        if without_domain := query_ldap(email_match.group(1)):  # If just the prefix WAS found
            for entry in without_domain:
                # If it doens't have an email, use query
                # Also, LDAP is dumb and return no emails as the literal string "[]"
                entry["email"] = entry["email"] if entry["email"] != "[]" else query
            return without_domain

    return responses


def display_name_to_first_last(display_name):
    """Fixes a display name to be "Firstname Lastname" in case it's "Lastname, Firstname"

    Args:
        author (str): The string to fix

    Returns:
        str: The correct author name.
    """
    if match := re.match(DISPLAY_NAME_PATTERN, display_name):
        return match.group(2) + " " + match.group(1)
    return display_name


def resolve_user(query, properties=None):
    """
    Gets all the information about a specific user.

    Args:
        query (str): The value to search for.
        properties (list): A list of properties to search for.
                           If None, defaults to `sAMAccountName` and `name` (for backwards compatibility).

    Returns:
        UserEntry: An object (not dict!) representing all the user's attributes.
                   Each attribute is an object of itself, so to access email for example, access "UserEntry.mail.value"
    """
    properties = properties or ["sAMAccountName", "name"]
    query_string = "".join([f"({query_prop}={query})" for query_prop in properties])
    server = Server(LDAP.server, port=LDAP.port, use_ssl=True)
    connection = Connection(server, user=LDAP.username, password=LDAP.password, auto_bind=True)
    connection.search(
        search_base=LDAP.baseedn,
        search_filter=(f"(&(objectcategory=person)(objectclass=user)(intelflags=1)(|{query_string}))"),
        attributes=["*"],
    )
    return connection.entries


def get_members_of_group(common_name: str, recursive=True):
    """
    Get all the members of a specific group.

    Args:
        common_name (str): The common name (CN) of the group to search for. Supports wildcards (`*`)
        recursive (bool): Whether to search for members in subgroups as well. Defaults to True.
                          If False, groups will appear as items in the final list.

    Returns:
        list<str>: A list of the usernames of all the members of the group.
    """
    server = Server(LDAP.server, port=LDAP.port, use_ssl=True)
    connection = Connection(server, user=LDAP.username, password=LDAP.password, auto_bind=True)
    connection.search(
        search_base=LDAP.baseedn,
        search_filter=f"(&(objectcategory=group)(objectclass=group)(cn={common_name}))",
        attributes=["member"],
    )

    if not connection.entries:
        return []

    members = [parse_distinguished_name(member) for member in connection.entries[0].member]
    final_list = []
    for member in members:
        if member["OU"] == "Groups" and recursive:
            final_list += get_members_of_group(member["CN"])
        else:
            final_list.append(member)

    return final_list


def parse_distinguished_name(distinguished_name: str) -> dict:
    """
    Parse a distinguished name into its components.

    Args:
        distinguished_name (str): The distinguished name to parse (e.g. CN=foo,OU=Bar,DC=example,DC=com)

    Returns:
        dict: A dictionary containing the components of the distinguished name.
    """
    components = re.split(r"(?<!\\),", distinguished_name)
    return {component.split("=")[0]: component.split("=")[1].replace("\\", "") for component in components}
