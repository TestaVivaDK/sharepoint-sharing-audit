#!/bin/bash
sed -i "s/__WEBAPP_FQDN__/${WEBAPP_FQDN}/" /usr/local/apache2/conf/extra/httpd-ssl.conf
sed -i "s/__NEO4J_BROWSER_FQDN__/${NEO4J_BROWSER_FQDN}/" /usr/local/apache2/conf/extra/httpd-ssl.conf
sed -i "s/__NEO4J_BOLT_FQDN__/${NEO4J_BOLT_FQDN}/" /usr/local/apache2/conf/extra/httpd-ssl.conf
sed -i "s/__NEO4J_ADMIN_RESTRICTED_IP__/${NEO4J_ADMIN_RESTRICTED_IP}/" /usr/local/apache2/conf/extra/httpd-ssl.conf

httpd-foreground