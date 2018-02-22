#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import logging
import subprocess
import time
from os import path, geteuid, getcwd, chdir

logger = logging.getLogger(__file__)
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
formatter = logging.Formatter('[ %(levelname)s ] %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

UPGRADE_DB_COMMAND = "/etc/ckan_init.d/upgrade_db.sh"
REBUILD_SEARCH_COMMAND = "/etc/ckan_init.d/run_rebuild_search.sh"


class ComposeContext:
    def __init__(self, compose_path):
        self.compose_path = compose_path

    def __enter__(self):
        self.current_path = getcwd()
        chdir(self.compose_path)  # Change to docker-compose file's directory

    def __exit__(self, type, value, traceback):
        chdir(self.current_path)  # Go back


def ask(question):
    try:
        _ask = raw_input
    except NameError:
        _ask = input
    return _ask("%s\n" % question)


def check_permissions():
    if geteuid() != 0:
        logging.error("Se necesitan permisos de root (sudo).")
        exit(1)


def check_docker():
    subprocess.check_call([
        "docker",
        "ps"
    ])


def check_compose():
    subprocess.check_call([
        "docker-compose",
        "--version",
    ])


def get_compose_file(base_path, download_url):
    compose_file = "latest.yml"
    compose_file_path = path.join(base_path, compose_file)
    subprocess.check_call([
        "curl",
        download_url,
        "--output",
        compose_file_path
    ])
    return compose_file_path


def fix_env_file(base_path):
    env_file = ".env"
    env_file_path = path.join(base_path, env_file)
    nginx_var = "NGINX_HOST_PORT"
    datastore_var = "DATASTORE_HOST_PORT"
    maildomain_var = "maildomain"
    with open(env_file_path, "r+a") as env_f:
        content = env_f.read()
        if nginx_var not in content:
            env_f.write("%s=%s\n" % (nginx_var, "80"))
        if datastore_var not in content:
            env_f.write("%s=%s\n" % (datastore_var, "8800"))
        if maildomain_var not in content:
            maildomain = ask("Por favor, ingrese su dominio para envío de emails (e.g.: myportal.com.ar): ")
            real_maildomain = maildomain.strip()
            if not real_maildomain:
                print("Ningun valor fue ingresado, usando valor por defecto: localhost")
                real_maildomain = "localhost"
            env_f.write("%s=%s\n" % (maildomain_var, real_maildomain))


def backup_database(base_path, compose_path):
    db_container = subprocess.check_output(["docker-compose", "-f", compose_path, "ps", "-q", "db"])
    db_container = db_container.decode("utf-8").strip()
    cmd = [
        "docker",
        "exec",
        db_container,
        "bash",
        "-lc",
        "env PGPASSWORD=$POSTGRES_PASSWORD pg_dump --format=custom -U $POSTGRES_USER $POSTGRES_DB",
    ]
    output = subprocess.check_output(cmd)
    dump_name = "%s-ckan.dump" % time.strftime("%d:%m:%Y:%H:%M:%S")
    dump = path.join(base_path, dump_name)
    with open(dump, "wb") as a_file:
        a_file.write(output)


def pull_application(compose_path):
    subprocess.check_call([
        "docker-compose",
        "-f",
        compose_path,
        "pull",
    ])


def reload_application(compose_path):
    subprocess.check_call([
        "docker-compose",
        "-f",
        compose_path,
        "up",
        "-d",
        "nginx",
    ])


def check_previous_installation(base_path):
    compose_file = "latest.yml"
    compose_file_path = path.join(base_path, compose_file)
    if not path.isfile(compose_file_path):
        logging.error("Por favor corra este comando en el mismo directorio donde instaló la aplicación")
        logging.error("No se encontró el archivo %s en el directorio actual" % compose_file)
        raise Exception("[ ERROR ] No se encontró una instalación.")


def post_update_commands(compose_path):
    try:
        subprocess.check_call(
            ["docker-compose",
             "-f",
             compose_path,
             "exec",
             "portal",
             "bash",
             "/etc/ckan_init.d/run_updates.sh"
             ]
        )
    except subprocess.CalledProcessError as e:
        logging.error("Error al correr el script 'run_updates.sh'")
        logging.error(e)
    all_plugins = subprocess.check_output(
        ["docker-compose",
         "-f",
         compose_path,
         "exec",
         "portal",
         "grep", "-E", "^ckan.plugins.*", "/etc/ckan/default/production.ini"]
    ).decode("utf-8").strip()
    subprocess.check_call(
        ["docker-compose",
         "-f",
         compose_path,
         "exec",
         "portal",
         "sed", "-i", "s/^ckan\.plugins.*/ckan.plugins = stats/", "/etc/ckan/default/production.ini"]
    )
    subprocess.check_call([
        "docker-compose",
        "-f",
        compose_path,
        "exec",
        "portal",
        UPGRADE_DB_COMMAND,
    ])
    subprocess.check_call(
        ["docker-compose",
         "-f",
         compose_path,
         "exec",
         "portal",
         "sed", "-i", "s/^ckan\.plugins.*/%s/" % all_plugins, "/etc/ckan/default/production.ini"]
    )
    subprocess.check_call([
        "docker-compose",
        "-f",
        compose_path,
        "exec",
        "portal",
        REBUILD_SEARCH_COMMAND,
    ])


def restart_apps(compose_path):
    subprocess.check_call([
        "docker-compose",
        "-f",
        compose_path,
        "restart",
    ])


def update_andino(cfg, compose_file_url):
    directory = cfg.install_directory
    logging.info("Comprobando permisos (sudo)")
    check_permissions()
    logging.info("Comprobando que docker esté instalado...")
    check_docker()
    logging.info("Comprobando que docker-compose este instalado...")
    check_compose()
    logging.info("Comprobando instalación previa...")
    check_previous_installation(directory)
    logging.info("Descargando archivos necesarios...")
    compose_file_path = get_compose_file(directory, compose_file_url)
    fix_env_file(directory)
    logging.info("Guardando base de datos...")
    with ComposeContext(directory):
        backup_database(directory, compose_file_path)
        logging.info("Actualizando la aplicación")
        pull_application(compose_file_path)
        reload_application(compose_file_path)
        logging.info("Corriendo comandos post-instalación")
        post_update_commands(compose_file_path)
        logging.info("Reiniciando")
        restart_apps(compose_file_path)
        logging.info("Listo.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Actualizar andino.')

    parser.add_argument('--branch', default='master')
    parser.add_argument('--install_directory', default='/etc/portal/')
    args = parser.parse_args()

    base_url = "https://raw.githubusercontent.com/datosgobar/portal-andino"
    branch = args.branch
    file_name = "latest.yml"

    update_andino(args, path.join(base_url, branch, file_name))