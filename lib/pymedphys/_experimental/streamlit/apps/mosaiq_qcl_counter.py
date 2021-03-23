# Copyright (C) 2021 Cancer Care Associates

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import collections
import datetime

from pymedphys._imports import altair as alt
from pymedphys._imports import dateutil, natsort
from pymedphys._imports import numpy as np
from pymedphys._imports import pandas as pd
from pymedphys._imports import streamlit as st

import pymedphys
from pymedphys._streamlit import categories
from pymedphys._streamlit.server import session as _session
from pymedphys._streamlit.utilities import config as st_config
from pymedphys._streamlit.utilities import mosaiq as st_mosaiq

CATEGORY = categories.ALPHA
TITLE = "Completed Mosaiq QCL Counter"


def main():
    config = st_config.get_config()
    session_state = _session.session_state(reset_widget_id=0)

    site_config_map = {}
    for site_config in config["site"]:
        site = site_config["name"]
        try:
            mosaiq_config = site_config["mosaiq"]
            location = mosaiq_config["physics_qcl_location"]
            hostname = mosaiq_config["hostname"]
            port = mosaiq_config["port"]
            alias = mosaiq_config["alias"]
        except KeyError:
            continue

        site_config_map[site] = {
            "location": location,
            "hostname": hostname,
            "port": port,
            "alias": alias,
        }

    configuration_keys = site_config_map.keys()
    if len(configuration_keys) == 0:
        st.warning(
            "The appropriate configuration items for this tool have not been provided."
        )
        st.stop()

    site_options = list(site_config_map.keys())

    st.write(
        """
        ## Site selection
        """
    )

    selected_sites = []
    for site in site_options:
        if st.checkbox(site_config_map[site]["alias"], value=True):
            selected_sites.append(site)

    connections = _get_connections(site_config_map, selected_sites)

    st.write("## Filters")

    st.write(
        """
        ### QCL Completion date range

        Defaults to between the start of last month and the start of
        the current month.
        """
    )

    now = datetime.datetime.now()

    start_of_last_month = _get_start_of_last_month(now)
    if st.button("Reset date range filtering"):
        session_state.reset_widget_id += 1

    one, two, three = st.beta_columns(3)

    chosen_start = one.date_input(
        "Start date",
        value=start_of_last_month,
        key=f"chosen_start-date_input-{session_state.reset_widget_id}",
    )

    default_delta_month = two.number_input(
        "Default number of months from start to end",
        min_value=0,
        value=1,
        key=f"default_delta_month-number_input-{session_state.reset_widget_id}",
    )

    next_month = chosen_start + dateutil.relativedelta.relativedelta(
        months=default_delta_month
    )

    chosen_end = three.date_input(
        "End date",
        value=next_month,
        key=f"chosen_end-date_input-{session_state.reset_widget_id}",
    )

    st.write(
        """
        ## Results
        """
    )

    all_results = _get_all_results(
        site_config_map, connections, chosen_start, chosen_end
    )

    concatenated_results = pd.concat(all_results.values())
    concatenated_results["actual_completed_time"] = concatenated_results[
        "actual_completed_time"
    ].astype("datetime64[ns]")
    chart = (
        alt.Chart(concatenated_results)
        .mark_bar()
        .encode(
            alt.X("yearmonthdate(actual_completed_time):T", bin=alt.Bin(maxbins=20)),
            alt.Y("count()"),
            alt.Color("task"),
        )
    ).interactive()
    st.altair_chart(chart, use_container_width=True)

    for site, results in all_results.items():
        st.write(f"### {site_config_map[site]['alias']}")
        st.write(results)

    markdown_counts = "# Counts\n\n"
    for task in natsort.natsorted(concatenated_results["task"].unique()):
        counts = np.sum(concatenated_results["task"] == task)
        markdown_counts += f"* {task}: `{counts}`\n"

    st.sidebar.write(markdown_counts)


def _get_connections(site_config_map, selected_sites):
    connections = {}

    for site in selected_sites:
        connection = _get_connection_for_site_config(site_config_map[site])
        connections[site] = connection

    return connections


def _get_connection_for_site_config(site_config):
    connection_config = {k: site_config[k] for k in ("hostname", "port", "alias")}
    return st_mosaiq.get_cached_mosaiq_connection(**connection_config)


def _get_all_results(site_config_map, connections, chosen_start, chosen_end):

    all_results = {}
    for site, connection in connections.items():
        site_config = site_config_map[site]
        location = site_config["location"]

        results = _get_qcls_by_date(connection, location, chosen_start, chosen_end)

        for column in ("due", "actual_completed_time"):
            results[column] = _pandas_convert_series_to_date(results[column])

        all_results[site] = results

    return all_results


def _pandas_convert_series_to_date(series: pd.Series):
    return series.map(lambda item: item.strftime("%Y-%m-%d"))


def _get_start_of_month(dt: datetime.datetime):
    return dt.replace(day=1)


def _get_start_of_last_month(dt: datetime.datetime):
    definitely_in_last_month = _get_start_of_month(dt) - datetime.timedelta(days=1)
    return _get_start_of_month(definitely_in_last_month)


def _get_qcls_by_date(connection, location, start, end):
    data = pymedphys.mosaiq.execute(
        connection,
        """
        SELECT
            Ident.IDA,
            Patient.Last_Name,
            Patient.First_Name,
            Chklist.Due_DtTm,
            Chklist.Act_DtTm,
            QCLTask.Description
        FROM Chklist, Staff, QCLTask, Ident, Patient
        WHERE
            Chklist.Pat_ID1 = Ident.Pat_ID1 AND
            Patient.Pat_ID1 = Ident.Pat_ID1 AND
            QCLTask.TSK_ID = Chklist.TSK_ID AND
            Staff.Staff_ID = Chklist.Rsp_Staff_ID AND
            RTRIM(LTRIM(Staff.Last_Name)) = RTRIM(LTRIM(%(location)s)) AND
            Chklist.Act_DtTm >= %(start)s AND
            Chklist.Act_DtTm < %(end)s
        """,
        {"location": location, "start": start, "end": end},
    )

    results = pd.DataFrame(
        data=data,
        columns=[
            "patient_id",
            "last_name",
            "first_name",
            "due",
            "actual_completed_time",
            "task",
        ],
    )

    results = results.sort_values(by=["actual_completed_time"], ascending=False)

    return results
