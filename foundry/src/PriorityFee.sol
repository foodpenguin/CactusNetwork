// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ProtocolTreasury} from "./ProtocolTreasury.sol";

contract PriorityFee is Ownable {

    event Pay(address indexed user, address indexed token, uint256 amount);

    ProtocolTreasury public protocolTreasury;

    constructor(address payable _Treasury) Ownable(msg.sender) {
        protocolTreasury = ProtocolTreasury(_Treasury);
    }

    function pay(address _token, uint256 _amount) external {
        require(_amount > 0, "Amount must be > 0");

        bool success = IERC20(_token).transferFrom(msg.sender, address(protocolTreasury), _amount);

        require(success, "Transfer failed");

        emit Pay(msg.sender, _token, _amount);
    }
}
