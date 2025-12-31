// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract AICollaboration {
    address public coordinator;
    bytes32 public globalModelHash;
    uint256 public currentRound = 0;
    bool public trainingActive = false;

    struct Contribution {
        bytes32 modelHash;
        bool isValidated;
        bool isPaid;
    }

    // Mapping per Round to allow automation without conflict
    mapping(uint256 => mapping(address => Contribution)) public contributions;

    event HashSubmitted(uint256 indexed round, address indexed participant, bytes32 modelHash);
    event RewardPaid(uint256 indexed round, address indexed participant, uint256 amount);
    event TrainingStarted(uint256 round);
    event TrainingFinished(uint256 round);

    constructor() { coordinator = msg.sender; }

    modifier onlyCoordinator() {
        require(msg.sender == coordinator, "Seul le coordinateur peut faire cela");
        _;
    }

    function startNewRound() public onlyCoordinator {
        currentRound++;
        trainingActive = true;
        emit TrainingStarted(currentRound);
    }

    function stopTraining() public onlyCoordinator {
        trainingActive = false;
        emit TrainingFinished(currentRound);
    }

    function submitUpdate(bytes32 _modelHash) public {
        require(trainingActive, "L'entrainement n'est pas actif actuellement");
        require(_modelHash != bytes32(0), "Hash invalide");
        contributions[currentRound][msg.sender] = Contribution({
            modelHash: _modelHash,
            isValidated: false,
            isPaid: false
        });
        emit HashSubmitted(currentRound, msg.sender, _modelHash);
    }

    function validateAndPay(address payable _participant) public payable onlyCoordinator {
        require(contributions[currentRound][_participant].modelHash != bytes32(0), "Aucune contribution trouvee");
        require(!contributions[currentRound][_participant].isPaid, "Deja paye");
        contributions[currentRound][_participant].isValidated = true;
        contributions[currentRound][_participant].isPaid = true;
        (bool success, ) = _participant.call{value: msg.value}("");
        require(success, "Echec transfert");
        emit RewardPaid(currentRound, _participant, msg.value);
    }
}