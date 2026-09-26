#!/usr/bin/env bash

# Author: Zhang Huangbin <zhb _at_ iredmail.org>

# -------------------------------------------
# Install all optional components.
# -------------------------------------------
optional_components()
{
    # iRedAPD.
    check_status_before_run iredapd_setup

    # iRedAdmin.

    # Roundcubemail.

    # SOGo

    # Fail2ban.
    [ X"${USE_FAIL2BAN}" == X'YES' -a X"${DISTRO}" != X'FREEBSD' ] && \
        check_status_before_run fail2ban_setup

    # netdata.
}
