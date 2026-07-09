if __name__ == "__main__":

    database = "WCD_Build_System"
    username = "WCD_Build_System_rw"
    password = "m8fG0TyAqQiDdU0!"
    instance_url = "sql1312-lc-in.ger.corp.intel.com"
    instance_port = "3181"
    server = f"{DBaaS.instance_url},{DBaaS.instance_port}"

    TABLE = 

    db = DBConnector(server, database, username, password)

    QUERY = "select * from bsod"

    
