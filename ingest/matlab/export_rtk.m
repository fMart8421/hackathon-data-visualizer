% EXPORT_RTK  Flatten the RTK NMEA capture tables into CSV.
%
%   The .mat files in data/GNSSresRTK hold MATLAB table objects (MCOS).
%   Nothing outside MATLAB can read them, so this script converts each
%   session into one CSV per NMEA sentence type, which the Python ingest then
%   loads like any other source file.
%
%   Just run it: press Run, or type  export_rtk  at the prompt. No arguments,
%   and it does not care what the current folder is, because every path is
%   resolved relative to this file.
%
%   Output lands in  data/export/<session>/<variable>.csv , for example
%   data/export/RTK_25Nov/ggaDataValid1.csv. Existing CSVs are overwritten.
%
%   Every table variable in every file is exported, so no column has to be
%   guessed here. The ones the dashboards need are Latitude, Longitude,
%   Altitude, UTCTime/UTCDateTime, QualityIndicator, NumSatellitesInUse,
%   HDOP/PDOP/VDOP, GroundSpeed, TrueCourseAngle, and the GSV
%   SatelliteID/Elevation/Azimuth used for a skyplot. Two extra columns are
%   added: source_file and session, so any row can be traced back.

%% Which sessions to export -----------------------------------------------
% Curated by default (DEC-19). These three carry what the map panels need:
% a base/rover pair and moving tracks. The other seven sessions are thousands
% of near-duplicate static captures, worth exporting only if a panel asks for
% them.
sessions = { ...
    'RTK_25Nov',     ...  %  17 Base + 17 Rove captures
    'RTK_BaseRover', ...  %   5 short-baseline occupations, stored as structs
    'RTK_27Nov'};         %  50 Base + 50 Rove captures

% Set true to sweep all ten sessions instead: 18703 files, several GB of
% tables, and a long wait. Memory is released between sessions, so the peak is
% set by the largest single session (RTK_Right, 3586 files) rather than by the
% total.
exportEverything = false;

% Lower this for a quick trial run, e.g. 5. The cap samples evenly across the
% session rather than taking the first N: files sort alphabetically, so all
% Data_1_Base_* come before all Data_2_Rove_*, and taking the first 5 would
% export only base captures and none of the rover.
maxFilesPerSession = 5;

%% Resolve paths ----------------------------------------------------------
thisDir  = fileparts(mfilename('fullpath'));
repoRoot = fullfile(thisDir, '..', '..');
dataRoot = fullfile(repoRoot, 'data', 'GNSSresRTK');
outRoot  = fullfile(repoRoot, 'data', 'export');

if ~isfolder(dataRoot)
    error('export_rtk:noData', 'Cannot find %s', dataRoot);
end

if exportEverything
    entries  = dir(dataRoot);
    entries  = entries([entries.isdir]);
    names    = {entries.name};
    sessions = names(~ismember(names, {'.', '..'}));
end

fprintf('exporting %d session(s) from %s\n\n', numel(sessions), dataRoot);

%% Export -----------------------------------------------------------------
totalFiles = 0;
totalRows  = 0;

for s = 1:numel(sessions)
    sessionDir = fullfile(dataRoot, sessions{s});
    outDir     = fullfile(outRoot, sessions{s});

    if ~isfolder(sessionDir)
        warning('export_rtk:missing', 'Skipping %s, no such folder', sessions{s});
        continue
    end

    fprintf('=== %s\n', sessions{s});
    [nFiles, nRows] = export_session(sessionDir, outDir, sessions{s}, maxFilesPerSession);
    totalFiles = totalFiles + nFiles;
    totalRows  = totalRows + nRows;
    fprintf('\n');
end

fprintf('done: %d files, %d rows, written under %s\n', totalFiles, totalRows, outRoot);

%% ------------------------------------------------------------------------
function [nFiles, nRows] = export_session(sessionDir, outDir, sessionName, maxFiles)
%EXPORT_SESSION Convert one session folder into one CSV per table variable.

    nFiles = 0;
    nRows  = 0;

    files = dir(fullfile(sessionDir, '*.mat'));
    if isempty(files)
        warning('export_rtk:empty', 'No .mat files in %s', sessionDir);
        return
    end
    if numel(files) > maxFiles
        % Spread the sample across the whole session. Taking files(1:maxFiles)
        % would take only Data_1_Base_* and miss every Data_2_Rove_*.
        pick  = unique(round(linspace(1, numel(files), maxFiles)));
        files = files(pick);
    end
    if ~isfolder(outDir)
        mkdir(outDir);
    end

    fprintf('  %d files\n', numel(files));
    collected = containers.Map();

    for k = 1:numel(files)
        path = fullfile(files(k).folder, files(k).name);
        try
            S = load(path);
        catch err
            warning('export_rtk:load', 'Skipping %s: %s', files(k).name, err.message);
            continue
        end
        nFiles = nFiles + 1;

        names = fieldnames(S);
        for n = 1:numel(names)
            % RTK_BaseRover stores a struct of equal-length arrays rather than
            % a table, so convert before the table path below.
            value = as_table(S.(names{n}));
            if isempty(value) || height(value) == 0
                continue    % the empty gst/hdt/zda placeholders land here
            end

            % Provenance, so a row can always be traced back to its capture.
            value.source_file = repmat(string(files(k).name), height(value), 1);
            value.session     = repmat(string(sessionName),   height(value), 1);

            key = names{n};
            if isKey(collected, key)
                previous = collected(key);
                if ~isequal(previous.Properties.VariableNames, value.Properties.VariableNames)
                    warning('export_rtk:schema', ...
                        'Column mismatch for %s in %s, skipping that table', ...
                        key, files(k).name);
                    continue
                end
                try
                    collected(key) = [previous; value];
                catch err
                    % Same columns, incompatible types: the RoverData captures
                    % store `time` as int32 in some files and uint16 in others.
                    warning('export_rtk:concat', ...
                        'Cannot append %s from %s: %s', key, files(k).name, err.message);
                end
            else
                collected(key) = value;
            end
        end

        if mod(k, 250) == 0
            fprintf('    %d/%d\n', k, numel(files));
        end
    end

    keys = collected.keys;
    for k = 1:numel(keys)
        tbl = collected(keys{k});
        target = fullfile(outDir, sprintf('%s.csv', keys{k}));
        writetable(tbl, target);
        nRows = nRows + height(tbl);
        fprintf('  wrote %-20s %8d rows\n', keys{k}, height(tbl));
    end

    clear collected    % release before the next session
end

%% ------------------------------------------------------------------------
function tbl = as_table(value)
%AS_TABLE Return a table for a table or a struct of equal-length arrays.
%   Anything else, including the empty uint8 placeholders MATLAB writes for
%   NMEA sentence types that were never received, comes back empty.

    tbl = [];

    if istable(value)
        tbl = value;
        return
    end

    if ~isstruct(value) || numel(value) ~= 1
        return
    end

    fields = fieldnames(value);
    if isempty(fields)
        return
    end

    columns = cell(1, numel(fields));
    rows = [];
    for i = 1:numel(fields)
        column = value.(fields{i});
        if ~isnumeric(column) && ~islogical(column) && ~isstring(column)
            return    % nested struct or cell: not a flat record set
        end
        column = column(:);    % the captures store row vectors
        if isempty(rows)
            rows = numel(column);
        elseif numel(column) ~= rows
            return    % ragged fields are not a table
        end
        columns{i} = column;
    end

    if isempty(rows) || rows == 0
        return
    end
    tbl = table(columns{:}, 'VariableNames', fields);
end
