from __future__ import absolute_import, division, print_function

import boto3
import cloudknot.aws
import configparser
import logging
import os

from . import aws

CONFIG = configparser.ConfigParser()

__all__ = ["get_config_file", "get_region", "set_region",
           "add_resource", "remove_resource", "verify_sections",
           "prune"]


def get_config_file():
    """Get the path to the cloudknot config file

    First, check for the CLOUDKNOT_CONFIG_FILE environment variable.
    If that fails, use ~/.aws/cloudknot. If ~/.aws/cloudknot doesn't
    exist, create it.

    Returns
    -------
    config_file : string
        Path to cloudknot config file
    """
    try:
        # Get config file from environment variable
        env_file = os.environ['CLOUDKNOT_CONFIG_FILE']
        config_file = os.path.abspath(env_file)
    except KeyError:
        # Fallback on default config file path
        home = os.path.expanduser('~')
        config_file = os.path.join(home, '.aws', 'cloudknot')

    if not os.path.isfile(config_file):
        # If the config file does not exist, create it
        with open(config_file, 'w') as f:
            f.write('# cloudknot configuration file')

        logging.info('Created new cloudknot config file at {path:s}'.format(
            path=config_file
        ))

    logging.info('Using cloudknot config file {path:s}'.format(
        path=config_file
    ))

    return config_file


def get_region():
    """Get the default AWS region

    First, check the cloudknot config file for the region option.
    If that fails, check for the AWS_DEFAULT_REGION environment variable.
    If that fails, use the region in the AWS (not cloudknot) config file.
    If that fails, use us-east-1.

    Returns
    -------
    region : string
        default AWS region
    """
    config_file = get_config_file()
    CONFIG.clear()
    CONFIG.read(config_file)

    if CONFIG.has_section('aws') and CONFIG.has_option('aws', 'region'):
        return CONFIG.get('aws', 'region')
    else:
        # Set `region`, the fallback region in case the cloudknot
        # config file has no region set
        try:
            # Get the region from an environment variable
            region = os.environ['AWS_DEFAULT_REGION']
        except KeyError:
            # Get the default region from the AWS config file
            home = os.path.expanduser('~')
            aws_config_file = os.path.join(home, '.aws', 'config')

            fallback_region = 'us-east-1'
            if os.path.isfile(aws_config_file):
                aws_config = configparser.ConfigParser()
                aws_config.read(aws_config_file)
                try:
                    region = aws_config.get(
                        'default', 'region', fallback=fallback_region
                    )
                except TypeError:
                    # python 2.7 compatibility
                    region = aws_config.get('default', 'region')
                    region = region if region else fallback_region
            else:
                region = fallback_region

        if not CONFIG.has_section('aws'):
            CONFIG.add_section('aws')

        CONFIG.set('aws', 'region', region)
        with open(config_file, 'w') as f:
            CONFIG.write(f)

        return region


def set_region(region='us-east-1'):
    """Set the AWS region that cloudknot will use

    Set region by modifying the cloudknot config file

    Parameters
    ----------
    region : string
        An AWS region

    Returns
    -------
    None
    """
    response = aws.clients['ec2'].describe_regions()
    region_names = [d['RegionName'] for d in response.get('Regions')]

    if region not in region_names:
        raise ValueError('`region` must be in {regions:s}'.format(
            str(region_names)
        ))

    config_file = get_config_file()
    CONFIG.clear()
    CONFIG.read(config_file)

    if not CONFIG.has_section('aws'):
        CONFIG.add_section('aws')

    CONFIG.set('aws', 'region', region)
    with open(config_file, 'w') as f:
        CONFIG.write(f)

    # Update the boto3 clients so that the region change is reflected
    # throughout the package
    aws.clients['iam'] = boto3.client('iam', region_name=region)
    aws.clients['ec2'] = boto3.client('ec2', region_name=region)
    aws.clients['batch'] = boto3.client('batch', region_name=region)
    aws.clients['ecr'] = boto3.client('ecr', region_name=region)


def add_resource(section, option, value):
    """Add a resource to the cloudknot config file

    Parameters
    ----------
    section : string
        Config section to which to add option:value

    option : string
        Config option to add (i.e. the key in the key:value pair)

    value : string
        Config value to add (i.e. second item in key:value pair)

    Returns
    -------
    None
    """
    config_file = get_config_file()
    CONFIG.clear()
    CONFIG.read(config_file)
    if section not in CONFIG.sections():
        CONFIG.add_section(section)
    CONFIG.set(section=section, option=option, value=value)
    with open(config_file, 'w') as f:
        CONFIG.write(f)


def remove_resource(section, option):
    """Remove a resource from the cloudknot config file

    Parameters
    ----------
    section : string
        Config section from which to remove option

    option : string
        Config option to remove (i.e. the key in the key:value pair)

    Returns
    -------
    None
    """
    config_file = get_config_file()
    CONFIG.clear()
    CONFIG.read(config_file)
    CONFIG.remove_option(section, option)
    with open(config_file, 'w') as f:
        CONFIG.write(f)


def verify_sections():
    """Verify config sections, remove ones that don't belong

    Returns
    -------
    None
    """
    config_file = get_config_file()
    CONFIG.clear()
    CONFIG.read(config_file)
    approved_sections = [
        'aws', 'roles', 'vpc', 'security-groups', 'docker-repos',
        'job-definitions', 'compute-environments', 'job-queues', 'jobs'
    ]

    def section_approved(sec):
        return any([
            sec in approved_sections,
            sec.split(' ', 1)[0] in ['pars', 'jars']
        ])

    for section in CONFIG.sections():
        if not section_approved(section):
            CONFIG.remove_section(section)


def prune():
    """Remove config items for non-existent AWS resources

    Returns
    -------
    None
    """
    verify_sections()

    config_file = get_config_file()
    CONFIG.clear()
    CONFIG.read(config_file)

    # Prune roles
    for role_name in CONFIG.options('roles'):
        try:
            cloudknot.aws.iam.IamRole(name=role_name)
        except cloudknot.aws.ResourceDoesNotExistException:
            CONFIG.remove_option('roles', role_name)

    # Prune VPCs
    for vpc_id in CONFIG.options('vpc'):
        try:
            cloudknot.aws.ec2.Vpc(vpc_id=vpc_id)
        except cloudknot.aws.ResourceDoesNotExistException:
            CONFIG.remove_option('vpc', vpc_id)

    # Prune security groups
    for sg_id in CONFIG.options('security-groups'):
        try:
            cloudknot.aws.ec2.SecurityGroup(security_group_id=sg_id)
        except cloudknot.aws.ResourceDoesNotExistException:
            CONFIG.remove_option('security-groups', sg_id)

    # Prune docker containers
    for repo in CONFIG.options('docker-repos'):
        pass

    # Prune job definitions
    for job_def_name in CONFIG.options('job-definitions'):
        try:
            cloudknot.aws.iam.IamRole(name=job_def_name)
        except cloudknot.aws.ResourceDoesNotExistException:
            CONFIG.remove_option('job-definitions', job_def_name)

    # Prune compute environments
    for ce_name in CONFIG.options('compute-environments'):
        try:
            cloudknot.aws.iam.IamRole(name=ce_name)
        except cloudknot.aws.ResourceDoesNotExistException:
            CONFIG.remove_option('compute-environments', ce_name)

    # Prune job queues
    for queue_name in CONFIG.options('job-queues'):
        try:
            cloudknot.aws.iam.IamRole(name=queue_name)
        except cloudknot.aws.ResourceDoesNotExistException:
            CONFIG.remove_option('job-queues', queue_name)

    # Prune batch jobs
    for job_id in CONFIG.options('jobs'):
        try:
            cloudknot.aws.iam.IamRole(job_id=job_id)
        except cloudknot.aws.ResourceDoesNotExistException:
            CONFIG.remove_option('jobs', job_id)

    # Prune pars
    # Prune jars
