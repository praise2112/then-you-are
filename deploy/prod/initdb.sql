\getenv app_db_password APP_DB_PASSWORD
create role oddstage login password :'app_db_password';
create database oddstage owner oddstage;
