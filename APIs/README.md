# Running Regression Tests

## Setting Up

Before you can run Pytest, you have to set up your environment like so:

1. Make sure you have Python 3.8 (or higher) installed ([download here](https://www.python.org/))

2. ***(Optional but recommended)*** Create a virtual environment:

   1. In the PotatoFarm repository root, in your preferred console, type: `python -m venv .venv`

      (Replace `python` with your executable, e.g. `python3`)

   2. Activate the virtual environment using:

      * `.\.venv\Scripts\Activate.ps1` in PowerShell
      * ` .venv\Scripts\activate.bat  ` in CMD
      * `source .venv/bin/activate` in Bash

3. Install the packages required by both the APIs and PyTest by typing (from the repository root directory)

   ```cmd
   pip install -r requirements.txt -r test/requirements.txt --proxy http://proxy-dmz.intel.com:912
   ```

   ### Troubleshooting
   If this stage fails on Windows, try installing the [Visual Studio 2019 Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2019) (Specifically the "Desktop Development with C++" option) and then running the above line again.

   If it hangs on `pygerrit2`, make sure you have the correct proxy settings in your environment variables:
   - For windows, open Command Prompt or PowerShell **as Administrator** and type:
     ```cmd
     setx /S HTTP_PROXY http://proxy-dmz.intel.com:912
     setx /S HTTPS_PROXY http://proxy-dmz.intel.com:912
     setx /S NO_PROXY=localhost,intel.com,127.0.0.1
     ```
   - For linux, edit `~/.bashrc` and add the lines:
     ```bash
     export http_proxy=http://proxy-dmz.intel.com:912
     export https_proxy=http://proxy-dmz.intel.com:912
     export no_proxy=localhost,intel.com,127.0.0.1
     ```


## Running the tests manually

To run all the tests, simply type the following from the repository root directory:

```
pytest
```

You should see output similar to this:

```
============================================================================================= test session starts =============================================================================================
platform win32 -- Python 3.9.5, pytest-6.2.4, py-1.10.0, pluggy-0.13.1
rootdir: C:\Users\eavron\repos\wifi_drv-devops-apis
plugins: mock-3.6.1
collected 14 items

test\test_artifactory.py ..........                                                                                                                                                                      [ 71%]
test\test_gerrit.py .                                                                                                                                                                                    [ 78%] 
test\test_ldap.py ...                                                                                                                                                                                    [100%]

============================================================================================= 14 passed in 0.35s ============================================================================================== 
```

## Running the tests in Visual Studio Code (recommended)

1. In Visual Studio Code, open the workspace directory (`CTRL+K, CTRL+O`).

2. Open the command pellet (`F1`) , type `>workspace` and select `Open Workspace Settings (JSON)`

3. Add the following line to the json:

   ```json
   {
   	...
   	"python.testing.pytestEnabled": true,
   	...
   }
   ```

4. Save and close it, and after a few seconds the tests icon should appear on your sidebar (Looks like a chemistry vial)

5. Click the tests icon, and there you can either run all tests (Double play button), run each test individually, or debug tests (by clicking the Debug icon)

Successful tests will appear green with a ✔️icon.
Failed tests will appear red (or gray) with a ❌(or ❔) icon.

If you debug tests, you can use breakpoints in either the tests or the code they're testing - which is very useful.



# Running Linters and Formatters checks

PerCI for the Devops APIs libraries also checks that your code is compliant with various formatting and linting conventions.
It is highly recommended that you install the linters and formatters for Visual Studio Code as described [here](https://wiki.ith.intel.com/display/WCDSherlock/Visual+Studio+Code#VisualStudioCode-LintersandFormatters).

## Setting Up

To run the tests as they would run in PerCI, do the following:

1. Make sure you have Python 3.8 (or higher) installed ([download here](https://www.python.org/))

2. ***(Optional but recommended)*** Create a virtual environment:

   1. In the PotatoFarm repository root, in your preferred console, type: `python -m venv .venv`

      (Replace `python` with your executable, e.g. `python3`)

   2. Activate the virtual environment using:

      * `.\.venv\Scripts\Activate.ps1` in PowerShell
      * ` .venv\Scripts\activate.bat  ` in CMD
      * `source .venv/bin/activate` in Bash

3. Install the packages required by both the APIs and PerCI by typing (from the repository root directory)

   ```
   pip install -r requirements.txt -r PerCI/requirements.txt --proxy http://proxy-dmz.intel.com:912
   ```

   If this stage fails on Windows, try installing the [Visual Studio 2019 Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2019) (Specifically the "Desktop Development with C++" option) and then running the above line again.

   

## Running the tests

From the root directory of the repository, in PowerShell run:

```
.\PerCI\test_formatting.ps1
```

You should see an output like this if everything is ok:

```
Testing GerritAPI.py ...        PASSED!
Testing LdapAPI.py ...  PASSED!
Testing test/test_gerrit.py ... PASSED!
Testing test/test_ldap.py ...   PASSED!All tests passed!
```

And if you have any problems, you'll see them listed per file:

```
Testing GerritAPI.py ...        PASSED!
Testing LdapAPI.py ...  FAILED!
************* Module LdapAPI
LdapAPI.py:17:0: C0116: Missing function or method docstring (missing-function-docstring)
LdapAPI.py:43:0: C0103: Function name "Foo" doesn't conform to snake_case naming style (invalid-name)

*** Black would reformat the file!

===
Testing test/test_gerrit.py ... PASSED!
Testing test/test_ldap.py ...   FAILED!
************* Module test.test_ldap
test\test_ldap.py:3:0: E0611: No name 'display_name_to_first_last' in module 'LdapAPI' (no-name-in-module)

===

Linting and formatting on your commit failed!
See https://wiki.ith.intel.com/display/WCDSherlock/Visual+Studio+Code#VisualStudioCode-Python for instructions on setting up linters and formatters in VSCode.
```

In this example the file `LdapAPI` has two errors - one in line 17 and one in line 43, and also it would be reformatted by Black.
You can also see this errors in real-time in VSCode by going to the "Problems" tab (`CTRL+SHIFT+M`).

The Pylint errors (The ones with line numbers) need to be fixed manually in the code.
The Black errors can either be solved by typing `python -m black <filename>` or by using Visual Studio Code's auto-formatter (see instructions [here](https://wiki.ith.intel.com/display/WCDSherlock/Visual+Studio+Code#VisualStudioCode-LintersandFormatters))



